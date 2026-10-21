"use strict";

// Node >= 17 ships a native DOMException global; the deprecated
// node-domexception package exists only to polyfill it. fetch-blob
// (via node-fetch -> gaxios -> @google/genai -> pi-ai) imports this
// package as a default CJS export, so re-export the native class.
module.exports = globalThis.DOMException;
