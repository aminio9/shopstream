/**
 * ShopStream Notification Service
 * ================================
 * Handles real-time notifications via WebSocket and processes
 * notification messages from RabbitMQ.
 */

const express = require("express");
const { createServer } = require("http");
const { WebSocketServer, WebSocket } = require("ws");
const cors = require("cors");
const amqp = require("amqplib");
const fs = require("fs");

const app = express();
const server = createServer(app);
const wss = new WebSocketServer({ server, path: "/ws" });

const PORT = process.env.PORT || 3000;

// ============================================
// Configuration (RabbitMQ)
// ============================================

function getEnv(name, fallback = "") {
  const v = process.env[name];
  return (v === undefined || v === null || v === "") ? String(fallback) : String(v);
}

function readSecretFile(path) {
  try {
    // trim() removes trailing newline common in Docker secrets
    return fs.readFileSync(path, "utf8").trim();
  } catch {
    return "";
  }
}

function getRabbitPassword() {
  // 1) direct env for local/dev
  const direct = getEnv("RABBITMQ_PASSWORD", "");
  if (direct) return direct;

  // 2) Swarm/Docker secrets file
  const filePath = getEnv("RABBITMQ_PASSWORD_FILE", "/run/secrets/rabbitmq_password");
  return readSecretFile(filePath);
}

function buildAmqpUrl() {
  const user = getEnv("RABBITMQ_USER", "guest");
  const pass = getRabbitPassword(); // IMPORTANT: no default here
  const host = getEnv("RABBITMQ_HOST", "rabbitmq");
  const port = getEnv("RABBITMQ_PORT", "5672");
  const vhost = getEnv("RABBITMQ_VHOST", "/"); // optional

  // If user is not guest, we require a real password
  if (user !== "guest" && !pass) {
    const secretPath = getEnv("RABBITMQ_PASSWORD_FILE", "/run/secrets/rabbitmq_password");
    throw new Error(
      `RabbitMQ password missing for user '${user}'. Set RABBITMQ_PASSWORD or mount secret at ${secretPath}`
    );
  }

  // Encode credentials (important if password has special characters)
  const encUser = encodeURIComponent(user);
  const encPass = encodeURIComponent(pass || "guest"); // guest is only ok when user=guest
  const encVhost = vhost === "/" ? "" : `/${encodeURIComponent(vhost.replace(/^\//, ""))}`;

  return `amqp://${encUser}:${encPass}@${host}:${port}${encVhost}`;
}

function rabbitDebugInfo() {
  const user = getEnv("RABBITMQ_USER", "guest");
  const host = getEnv("RABBITMQ_HOST", "rabbitmq");
  const port = getEnv("RABBITMQ_PORT", "5672");
  const secretPath = getEnv("RABBITMQ_PASSWORD_FILE", "/run/secrets/rabbitmq_password");

  const envPassLen = getEnv("RABBITMQ_PASSWORD", "").length;

  let secretLen = 0;
  try {
    secretLen = fs.readFileSync(secretPath, "utf8").trim().length;
  } catch {
    secretLen = 0;
  }

  return { user, host, port, secretPath, envPassLen, secretLen };
}

let config = { rabbitmq: { url: "" } };
try {
  config.rabbitmq.url = buildAmqpUrl();
} catch (e) {
  // Log why config is invalid, but keep service up (health will be degraded)
  console.error(`[RabbitMQ] config error: ${e.message}`);
}

// Always log debug info (NO password printed)
const dbg = rabbitDebugInfo();
console.log(
  `[RabbitMQ] user=${dbg.user} host=${dbg.host}:${dbg.port} envPassLen=${dbg.envPassLen} secretPath=${dbg.secretPath} secretLen=${dbg.secretLen}`
);

// ============================================
// WebSocket Management
// ============================================

// Map of userId -> Set of WebSocket connections
const userConnections = new Map();

// Ping interval to keep connections alive
const PING_INTERVAL = 30000;

wss.on("connection", (ws) => {
  console.log("New WebSocket connection");

  let userId = null;

  ws.on("message", (data) => {
    try {
      const message = JSON.parse(data.toString());
      if (message.type === "auth" && message.userId) {
        userId = message.userId.toString();

        if (!userConnections.has(userId)) {
          userConnections.set(userId, new Set());
        }
        userConnections.get(userId).add(ws);

        console.log(`User ${userId} authenticated`);

        ws.send(
          JSON.stringify({
            type: "auth_success",
            message: "Connected to notification service",
          })
        );
      }
    } catch (error) {
      console.error("Failed to parse WebSocket message:", error);
    }
  });

  ws.on("close", () => {
    console.log("WebSocket connection closed");
    if (userId && userConnections.has(userId)) {
      userConnections.get(userId).delete(ws);
      if (userConnections.get(userId).size === 0) {
        userConnections.delete(userId);
      }
    }
  });

  ws.on("error", (error) => {
    console.error("WebSocket error:", error);
  });

  ws.send(
    JSON.stringify({
      type: "connected",
      message: "Welcome to ShopStream notifications",
      timestamp: new Date().toISOString(),
    })
  );
});

setInterval(() => {
  wss.clients.forEach((ws) => {
    if (ws.readyState === WebSocket.OPEN) ws.ping();
  });
}, PING_INTERVAL);

// ============================================
// Send Notification Functions
// ============================================

function sendToUser(userId, message) {
  const connections = userConnections.get(userId.toString());
  if (!connections || connections.size === 0) {
    console.log(`No active connections for user ${userId}`);
    return false;
  }

  const payload = JSON.stringify(message);
  let sent = 0;

  connections.forEach((ws) => {
    if (ws.readyState === WebSocket.OPEN) {
      ws.send(payload);
      sent++;
    }
  });

  console.log(`Sent notification to ${sent} connections for user ${userId}`);
  return sent > 0;
}

function broadcast(message) {
  const payload = JSON.stringify(message);
  let sent = 0;

  wss.clients.forEach((ws) => {
    if (ws.readyState === WebSocket.OPEN) {
      ws.send(payload);
      sent++;
    }
  });

  console.log(`Broadcast notification to ${sent} connections`);
  return sent;
}

// ============================================
// RabbitMQ Consumer
// ============================================

let rabbitConnection = null;
let rabbitChannel = null;
let reconnectTimer = null;

async function connectRabbitMQ() {
  // If config was invalid at startup (missing password), retry building config now
  try {
    config.rabbitmq.url = buildAmqpUrl();
  } catch (e) {
    console.error(`[RabbitMQ] ${e.message}`);
    scheduleReconnect();
    return false;
  }

  try {
    rabbitConnection = await amqp.connect(config.rabbitmq.url);
    rabbitChannel = await rabbitConnection.createChannel();

    await rabbitChannel.assertQueue("order_notifications", { durable: true });

    console.log("✓ RabbitMQ connected");

    rabbitChannel.consume("order_notifications", (msg) => {
      if (!msg) return;
      try {
        const content = JSON.parse(msg.content.toString());
        console.log("Received notification:", content);

        processNotification(content);
        rabbitChannel.ack(msg);
      } catch (error) {
        console.error("Failed to process notification:", error);
        rabbitChannel.nack(msg, false, false);
      }
    });

    rabbitConnection.on("error", (error) => {
      console.error("RabbitMQ connection error:", error.message || error);
      scheduleReconnect();
    });

    rabbitConnection.on("close", () => {
      console.log("RabbitMQ connection closed, reconnecting...");
      scheduleReconnect();
    });

    return true;
  } catch (error) {
    console.error("Failed to connect to RabbitMQ:", error.message);
    scheduleReconnect();
    return false;
  }
}

function scheduleReconnect() {
  if (reconnectTimer) return;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    connectRabbitMQ();
  }, 5000);
}

function processNotification(notification) {
  const { type, userId, ...data } = notification;

  const message = {
    type,
    ...data,
    timestamp: new Date().toISOString(),
  };

  switch (type) {
    case "order_created":
      message.title = "Order Placed!";
      message.body = `Your order #${data.orderId} has been placed successfully.`;
      break;

    case "order_update":
      message.title = "Order Update";
      message.body = `Your order #${data.orderId} status: ${data.status}`;
      break;

    case "order_cancelled":
      message.title = "Order Cancelled";
      message.body = `Your order #${data.orderId} has been cancelled.`;
      break;

    case "promotion":
      broadcast(message);
      return;

    default:
      console.log("Unknown notification type:", type);
  }

  if (userId) sendToUser(userId, message);
}

// ============================================
// HTTP Routes
// ============================================

app.use(cors());
app.use(express.json());

app.get("/health", (req, res) => {
  const health = {
    status: "healthy",
    service: "notification-service",
    timestamp: new Date().toISOString(),
    websocket: {
      connections: wss.clients.size,
      users: userConnections.size,
    },
  };

  if (rabbitConnection && rabbitChannel) {
    health.rabbitmq = "connected";
  } else {
    health.rabbitmq = "disconnected";
    health.status = "degraded";
  }

  res.status(health.status === "healthy" ? 200 : 503).json(health);
});

app.post("/notify", (req, res) => {
  const { userId, type, data } = req.body;

  if (!type) return res.status(400).json({ error: "Notification type is required" });

  const message = { type, ...(data || {}), timestamp: new Date().toISOString() };

  if (userId) {
    const sent = sendToUser(userId, message);
    return res.json({
      success: sent,
      message: sent ? "Notification sent" : "User not connected",
    });
  }

  const count = broadcast(message);
  res.json({ success: true, recipients: count });
});

app.post("/broadcast", (req, res) => {
  const { message, type = "announcement" } = req.body;
  if (!message) return res.status(400).json({ error: "Message is required" });

  const count = broadcast({ type, message, timestamp: new Date().toISOString() });
  res.json({ success: true, recipients: count });
});

app.get("/stats", (req, res) => {
  const stats = {
    totalConnections: wss.clients.size,
    authenticatedUsers: userConnections.size,
    connectionsByUser: {},
  };

  userConnections.forEach((connections, userId) => {
    stats.connectionsByUser[userId] = connections.size;
  });

  res.json(stats);
});

app.use((err, req, res, next) => {
  console.error("Error:", err);
  res.status(500).json({ error: "Internal server error" });
});

app.use((req, res) => res.status(404).json({ error: "Endpoint not found" }));

// ============================================
// Graceful Shutdown
// ============================================

async function shutdown() {
  console.log("\nShutting down gracefully...");

  wss.clients.forEach((ws) => ws.close(1000, "Server shutting down"));

  try {
    if (rabbitChannel) await rabbitChannel.close();
  } catch {}
  try {
    if (rabbitConnection) await rabbitConnection.close();
  } catch {}

  server.close(() => {
    console.log("Server closed");
    process.exit(0);
  });
}

process.on("SIGTERM", shutdown);
process.on("SIGINT", shutdown);

// ============================================
// Start Server
// ============================================

server.listen(PORT, "0.0.0.0", () => {
  console.log(`
╔═══════════════════════════════════════════════╗
║     ShopStream Notification Service           ║
╠═══════════════════════════════════════════════╣
║  HTTP Port: ${PORT}                              ║
║  WebSocket: /ws                               ║
║  Health: /health                              ║
╚═══════════════════════════════════════════════╝
  `);

  connectRabbitMQ();
});
