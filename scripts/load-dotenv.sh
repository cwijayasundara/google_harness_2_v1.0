# Fill unset credential variables from ./.env. Source this file, then call load_dotenv.
# A value already exported in the shell wins. Values are not printed.
load_dotenv() {
  local file="${1:-.env}"
  [ -f "$file" ] || return 0
  local line key val
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    case "$line" in
      ''|\#*) continue ;;
    esac
    case "$line" in
      export\ *) line="${line#export }" ;;
    esac
    key="${line%%=*}"
    case "$key" in
      FIREWORKS_API_KEY) ;;
      *) continue ;;
    esac
    [ -n "${!key:-}" ] && continue
    val="${line#*=}"
    val="${val#"${val%%[![:space:]]*}"}"
    val="${val%"${val##*[![:space:]]}"}"
    if [ "${#val}" -ge 2 ]; then
      case "$val" in
        \"*\") val="${val#\"}"; val="${val%\"}" ;;
        \'*\') val="${val#\'}"; val="${val%\'}" ;;
      esac
    fi
    [ -n "$val" ] || continue
    export "$key=$val"
  done < "$file"
}
