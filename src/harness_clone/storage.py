"""Transactional run metadata and immutable, digest-verified artifacts."""

import hashlib
import json
import shutil
import sqlite3
import tempfile
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

from .bundles import bundle_digest, validate_bundle
from .contracts import ExecutionRecord, ExperimentConfig, Usage


def fingerprint(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


class Database:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS records (kind TEXT, id TEXT, value TEXT NOT NULL, PRIMARY KEY(kind,id))"
            )

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def get(self, kind, key):
        with self.transaction() as db:
            row = db.execute(
                "SELECT value FROM records WHERE kind=? AND id=?", (kind, key)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, kind, key, value):
        with self.transaction() as db:
            db.execute(
                "INSERT OR REPLACE INTO records VALUES(?,?,?)",
                (kind, key, json.dumps(value, sort_keys=True, default=str)),
            )


class RunStore(Database):
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        super().__init__(self.root / "run.sqlite")
        (self.root / "bundles").mkdir(exist_ok=True)

    def create_experiment(self, config: ExperimentConfig) -> str:
        data = config.model_dump(mode="json")
        identity = fingerprint(data)
        with self.transaction() as db:
            row = db.execute(
                "SELECT value FROM records WHERE kind='experiment' AND id='config'"
            ).fetchone()
            if row and json.loads(row[0]) != data:
                raise ValueError("incompatible experiment configuration; use a new run directory")
            db.execute(
                "INSERT OR IGNORE INTO records VALUES('experiment','config',?)",
                (json.dumps(data, sort_keys=True),),
            )
        return identity

    def bundle_path(self, digest: str) -> Path:
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("invalid bundle digest")
        return self.root / "bundles" / digest

    def publish_bundle(self, root: Path) -> str:
        validate_bundle(root)
        digest = bundle_digest(root)
        dest = self.bundle_path(digest)
        if dest.exists():
            if bundle_digest(dest) != digest:
                raise ValueError("stored bundle digest mismatch")
            return digest
        with tempfile.TemporaryDirectory(dir=self.root / "bundles") as tmp:
            stage = Path(tmp) / "bundle"
            shutil.copytree(root, stage)
            try:
                stage.rename(dest)
            except OSError:
                if not dest.exists() or bundle_digest(dest) != digest:
                    raise
        return digest

    def save_execution(self, record: ExecutionRecord) -> None:
        value = record.model_dump(mode="json")
        with self.transaction() as db:
            row = db.execute(
                "SELECT value FROM records WHERE kind='execution' AND id=?", (record.execution_id,)
            ).fetchone()
            if row:
                old = ExecutionRecord.model_validate_json(row[0])
                if old.status == "completed" and old != record:
                    raise ValueError("completed execution is immutable")
            db.execute(
                "INSERT OR REPLACE INTO records VALUES('execution',?,?)",
                (record.execution_id, json.dumps(value, sort_keys=True)),
            )

    def completed_execution(self, execution_id: str) -> ExecutionRecord | None:
        value = self.get("execution", execution_id)
        if value is None:
            return None
        record = ExecutionRecord.model_validate(value)
        if record.status != "completed":
            return None
        if bundle_digest(self.bundle_path(record.bundle_digest)) != record.bundle_digest:
            raise ValueError("stored bundle digest mismatch")
        return record


class BudgetLedger(Database):
    def __init__(self, path: Path, cap: Decimal):
        super().__init__(path)
        self.cap = cap
        with self.transaction() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS budget (id TEXT PRIMARY KEY, ceiling TEXT NOT NULL, usage TEXT)"
            )

    def reserve(self, stage_id: str, ceiling_usd: Decimal) -> None:
        if ceiling_usd <= 0:
            raise ValueError("reservation must be positive")
        with self.transaction() as db:
            rows = db.execute("SELECT id,ceiling,usage FROM budget").fetchall()
            for name, ceiling, usage in rows:
                if name == stage_id:
                    if Decimal(ceiling) != ceiling_usd:
                        raise ValueError("reservation amount changed")
                    return
            total = Decimal(0)
            for _, ceiling, encoded in rows:
                usage = Usage.model_validate_json(encoded) if encoded else None
                if usage and not usage.known:
                    raise ValueError("unknown provider usage blocks further paid work")
                total += usage.usd if usage else Decimal(ceiling)
            if total + ceiling_usd > self.cap:
                raise ValueError("experiment spending cap exhausted")
            db.execute("INSERT INTO budget VALUES(?,?,NULL)", (stage_id, str(ceiling_usd)))

    def settle(self, stage_id: str, usage: Usage) -> None:
        with self.transaction() as db:
            row = db.execute("SELECT usage FROM budget WHERE id=?", (stage_id,)).fetchone()
            if row is None:
                raise ValueError("unreserved stage")
            if row[0] and Usage.model_validate_json(row[0]) != usage:
                raise ValueError("conflicting settlement")
            db.execute("UPDATE budget SET usage=? WHERE id=?", (usage.model_dump_json(), stage_id))

    def summary(self) -> dict:
        with self.transaction() as db:
            rows = db.execute("SELECT ceiling,usage FROM budget").fetchall()
        usages = [Usage.model_validate_json(u) for _, u in rows if u]
        return {
            "cap": str(self.cap),
            "spent": str(sum((u.usd for u in usages), Decimal(0))),
            "reserved": str(sum((Decimal(c) for c, u in rows if not u), Decimal(0))),
            "unknown": any(not u.known for u in usages),
        }

    def usage_for(self, stage_id: str) -> Usage:
        with self.transaction() as db:
            rows = db.execute("SELECT id,usage FROM budget").fetchall()
        selected = [encoded for name, encoded in rows if name.startswith(stage_id + "/")]
        values = [Usage.model_validate_json(encoded) for encoded in selected if encoded]
        return Usage(
            input_tokens=sum(u.input_tokens for u in values),
            cached_tokens=sum(u.cached_tokens for u in values),
            output_tokens=sum(u.output_tokens for u in values),
            usd=sum((u.usd for u in values), Decimal(0)),
            known=all(selected) and all(u.known for u in values),
        )
