/**
 * mookquant * Model Service
 *
 * Manages the model_server.py HTTP subprocess and provides
 * proxy methods for IPC layer consumption.
 *
 * Lifecycle: init() -> spawn model_server.py -> poll /health -> ready
 */
const { spawn } = require("child_process");
const path = require("path");
const fs = require("fs");
const http = require("http");

class ModelService {
  constructor(options = {}) {
    this._options = options;
    this._proc = null;
    this._port = options.port || 8765;
    this._ready = false;
    this._initPromise = null;
    this._baseUrl = "http://127.0.0.1:" + this._port;
  }

  get port() { return this._port; }
  get ready() { return this._ready; }

  /**
   * Start the model server subprocess.
   * @returns {Promise<void>}
   */
  async init() {
    if (this._initPromise) return this._initPromise;
    this._initPromise = this._spawn();
    return this._initPromise;
  }

  _spawn() {
    return new Promise((resolve, reject) => {
      const py = process.env.MOOKQUANT_PYTHON || this._options.python || "python";
      const script = path.join(__dirname, "..", "..", "bridge", "model_server.py");

      if (!fs.existsSync(script)) {
        return reject(new Error("model server script not found: " + script));
      }

      const env = Object.assign({}, process.env, {
        PYTHONIOENCODING: "utf-8",
        PYTHONUTF8: "1",
        MODEL_SERVER_PORT: String(this._port),
      });

      let proc;
      try {
        proc = spawn(py, [script], { stdio: ["pipe", "pipe", "pipe"], env });
      } catch (e) {
        return reject(new Error("failed to spawn python: " + e.message));
      }
      this._proc = proc;

      proc.stderr.on("data", (chunk) => {
        process.stderr.write("[model-server] " + chunk.toString());
      });

      proc.stdout.on("data", (chunk) => {
        process.stdout.write("[model-server] " + chunk.toString());
      });

      proc.on("error", (err) => {
        if (!this._ready) reject(err);
        this._ready = false;
      });

      proc.on("exit", (code, signal) => {
        process.stderr.write("[model-server] exited code=" + code + " signal=" + signal + "\n");
        this._ready = false;
        this._proc = null;
      });

      // Poll /health until the server is ready
      this._waitForReady(resolve, reject);
    });
  }

  _waitForReady(resolve, reject) {
    let attempts = 0;
    const maxAttempts = 30; // 30 * 200ms = 6s
    const check = () => {
      this._httpGet("/health")
        .then(() => {
          this._ready = true;
          console.log("[model-server] ready on port " + this._port);
          resolve();
        })
        .catch(() => {
          attempts++;
          if (attempts >= maxAttempts) {
            reject(new Error("model server startup timeout"));
          } else {
            setTimeout(check, 200);
          }
        });
    };
    setTimeout(check, 500); // give Python a moment to start
  }

  // ---- HTTP helpers ----

  _httpGet(p) {
    return new Promise((resolve, reject) => {
      const req = http.get(this._baseUrl + p, (res) => {
        let data = "";
        res.on("data", (c) => { data += c; });
        res.on("end", () => {
          try { resolve(JSON.parse(data)); }
          catch (e) { reject(new Error("JSON parse error: " + data.slice(0, 200))); }
        });
      });
      req.on("error", reject);
      req.setTimeout(5000, () => { req.destroy(); reject(new Error("request timeout")); });
    });
  }

  _httpPost(p, body) {
    return new Promise((resolve, reject) => {
      const bodyStr = JSON.stringify(body);
      const req = http.request(this._baseUrl + p, {
        method: "POST",
        headers: { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(bodyStr) },
      }, (res) => {
        let data = "";
        res.on("data", (c) => { data += c; });
        res.on("end", () => {
          try { resolve(JSON.parse(data)); }
          catch (e) { reject(new Error("JSON parse error: " + data.slice(0, 200))); }
        });
      });
      req.on("error", reject);
      req.setTimeout(60000, () => { req.destroy(); reject(new Error("request timeout")); });
      req.write(bodyStr);
      req.end();
    });
  }

  _httpPut(p, body) {
    return new Promise((resolve, reject) => {
      const bodyStr = JSON.stringify(body);
      const req = http.request(this._baseUrl + p, {
        method: "PUT",
        headers: { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(bodyStr) },
      }, (res) => {
        let data = "";
        res.on("data", (c) => { data += c; });
        res.on("end", () => {
          try { resolve(JSON.parse(data)); }
          catch (e) { reject(new Error("JSON parse error: " + data.slice(0, 200))); }
        });
      });
      req.on("error", reject);
      req.setTimeout(5000, () => { req.destroy(); reject(new Error("request timeout")); });
      req.write(bodyStr);
      req.end();
    });
  }

  _httpDelete(p) {
    return new Promise((resolve, reject) => {
      const req = http.request(this._baseUrl + p, { method: "DELETE" }, (res) => {
        let data = "";
        res.on("data", (c) => { data += c; });
        res.on("end", () => {
          try { resolve(JSON.parse(data)); }
          catch (e) { reject(new Error("JSON parse error: " + data.slice(0, 200))); }
        });
      });
      req.on("error", reject);
      req.setTimeout(5000, () => { req.destroy(); reject(new Error("request timeout")); });
      req.end();
    });
  }

  // ---- Public API (returns { ok, data? / error? }) ----

  async status() {
    if (!this._ready) {
      return { ok: false, error: "not ready", data: { ready: false, port: this._port } };
    }
    try {
      const r = await this._httpGet("/health");
      return { ok: true, data: { ready: true, port: this._port, strategies: r.strategies, models: r.models } };
    } catch (e) {
      return { ok: false, error: e.message, data: { ready: false, port: this._port } };
    }
  }

  async listStrategies() {
    return { ok: true, data: await this._httpGet("/strategies") };
  }

  async getStrategy(name) {
    return { ok: true, data: await this._httpGet("/strategy/" + encodeURIComponent(name)) };
  }

  async addStrategy(payload) {
    return { ok: true, data: await this._httpPost("/strategy", payload) };
  }

  async deleteStrategy(name) {
    return { ok: true, data: await this._httpDelete("/strategy/" + encodeURIComponent(name)) };
  }

  async listModels() {
    return { ok: true, data: await this._httpGet("/models") };
  }

  async getModel(id) {
    return { ok: true, data: await this._httpGet("/models/" + encodeURIComponent(id)) };
  }

  async deleteModel(id) {
    return { ok: true, data: await this._httpDelete("/models/" + encodeURIComponent(id)) };
  }
  async updateModel(id, patch) {
    return { ok: true, data: await this._httpPut("/models/" + encodeURIComponent(id), patch) };
  }

  async activateModel(id, force = false) {
    return { ok: true, data: await this._httpPost("/models/" + encodeURIComponent(id) + "/activate", { force }) };
  }

  async getActiveModel() {
    return { ok: true, data: await this._httpGet("/models/active") };
  }


  async startTraining(config) {
    return { ok: true, data: await this._httpPost("/train", config) };
  }

  async getTrainingStatus(taskId) {
    return { ok: true, data: await this._httpGet("/train/" + encodeURIComponent(taskId) + "/status") };
  }

  async computeSignal(payload) {
    return { ok: true, data: await this._httpPost("/signal", payload) };
  }

  dispose() {
    if (this._proc) {
      try { this._proc.kill(); } catch (e) {}
      this._proc = null;
    }
    this._ready = false;
    this._initPromise = null;
  }
}

module.exports = { ModelService };