#!/usr/bin/env node
/**
 * agent8088 WhatsApp Bridge (text-only)
 *
 * Standalone Node.js process that connects to WhatsApp via Baileys
 * and exposes a local HTTP API for the Python WhatsAppAdapter.
 *
 * Endpoints (loopback only, 127.0.0.1):
 *   GET  /health    — {status, queueLength, uptime, scriptHash}
 *   GET  /messages  — drains inbound message queue
 *   POST /send      — {chatId, message, replyTo?} → {success, messageId}
 *   POST /edit      — {chatId, messageId, message} → {success}
 *
 * Modes:
 *   --mode self-chat  (default) Only accept messages from your own self-chat.
 *                     Strangers are silently dropped at the bridge.
 *   --mode bot        Accept messages from anyone (Python allowlist gates access).
 *
 * LID resolution:
 *   WhatsApp uses opaque LIDs (e.g. 23425456279692@lid) instead of phone
 *   numbers. This bridge reads lid-mapping-*.json from the session dir to
 *   resolve LIDs → phone numbers, so the Python adapter always sees
 *   human-readable phone numbers for allowlist matching.
 *
 * Pairing:
 *   node bridge.js --pair --session <dir>
 *   Prints QR to terminal. Scan with WhatsApp → Linked Devices → Link a Device.
 *
 * Ported from hermes-agent's bridge.js, pruned to text-only for agent8088 v1.
 */

import express from 'express';
import qrcode from 'qrcode-terminal';
import pino from 'pino';
import crypto from 'crypto';
import path from 'path';
import fs from 'fs';
import os from 'os';
import { fileURLToPath } from 'url';
import { Boom } from '@hapi/boom';
import {
  makeWASocket,
  useMultiFileAuthState,
  fetchLatestBaileysVersion,
  DisconnectReason,
} from '@whiskeysockets/baileys';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const logger = pino({ level: 'warn' });

// --- Config from CLI args / env ---
function getArg(name, fallback) {
  const idx = process.argv.indexOf(`--${name}`);
  if (idx !== -1 && process.argv[idx + 1]) return process.argv[idx + 1];
  return fallback;
}

const PORT = parseInt(getArg('port', process.env.WHATSAPP_BRIDGE_PORT || '3000'), 10);
const SESSION_DIR = getArg('session', path.join(os.homedir(), '.local', 'share', 'agent8088', 'whatsapp', 'session'));
const PAIR_ONLY = process.argv.includes('--pair');
const WHATSAPP_MODE = getArg('mode', process.env.WHATSAPP_MODE || 'self-chat');
const MAX_QUEUE_SIZE = 100;
const MAX_MESSAGE_LENGTH = 4096;
const SEND_TIMEOUT_MS = 60000;
const CHUNK_DELAY_MS = 300;

// Script hash for staleness detection
const scriptHash = crypto.createHash('sha256').update(fs.readFileSync(__dirname + '/bridge.js')).digest('hex').slice(0, 16);

// --- State ---
let sock = null;
let connectionState = 'disconnected';
const messageQueue = [];
const startTime = Date.now();

// Echo-loop prevention
const recentlySentIds = new Set();
const SENT_ID_TTL = 512;
function trackSentId(id) {
  recentlySentIds.add(id);
  if (recentlySentIds.size > SENT_ID_TTL) {
    const first = recentlySentIds.values().next().value;
    recentlySentIds.delete(first);
  }
}

// --- LID → phone resolution (ported from hermes) ---
// Reads lid-mapping-{phone}.json files from session dir.
// Each file contains the LID for that phone number.
// _reverse files (lid-mapping-{lid}_reverse.json) contain the phone for that LID.
let lidToPhone = {};

function buildLidMap() {
  const map = {};
  try {
    for (const f of fs.readdirSync(SESSION_DIR)) {
      const m = f.match(/^lid-mapping-(\d+)\.json$/);
      if (!m) continue;
      const phone = m[1];
      const lid = JSON.parse(fs.readFileSync(path.join(SESSION_DIR, f), 'utf8'));
      if (lid) map[String(lid)] = phone;
    }
  } catch {}
  return map;
}

function reloadLidMap() {
  lidToPhone = buildLidMap();
  console.log(`LID map: ${Object.keys(lidToPhone).length} entries`);
}

/**
 * Resolve a WhatsApp JID or bare ID to a phone number.
 * If it's a LID (in our map), return the mapped phone.
 * If it's already a phone JID (xxx@s.whatsapp.net), strip to bare digits.
 * Otherwise return the bare ID unchanged.
 */
function resolveToPhone(jidOrId) {
  if (!jidOrId) return '';
  const bare = String(jidOrId).replace(/:.*@/, '@').replace(/@.*/, '').replace(/^\+/, '');
  if (lidToPhone[bare]) return lidToPhone[bare];
  return bare;
}

/**
 * Expand an identifier to all its aliases (LID + phone) by walking
 * the lid-mapping files transitively. Returns a Set of all known forms.
 */
function expandAliases(identifier) {
  const normalized = String(identifier || '').trim().replace(/:.*@/, '@').replace(/@.*/, '').replace(/^\+/, '');
  if (!normalized) return new Set();
  const resolved = new Set();
  const queue = [normalized];
  while (queue.length > 0) {
    const current = queue.shift();
    if (!current || resolved.has(current)) continue;
    resolved.add(current);
    for (const suffix of ['', '_reverse']) {
      const mappingPath = path.join(SESSION_DIR, `lid-mapping-${current}${suffix}.json`);
      if (!fs.existsSync(mappingPath)) continue;
      try {
        const mapped = JSON.parse(fs.readFileSync(mappingPath, 'utf8'));
        const normalizedMapped = String(mapped || '').replace(/@.*/, '').replace(/^\+/, '');
        if (normalizedMapped && !resolved.has(normalizedMapped)) {
          queue.push(normalizedMapped);
        }
      } catch {}
    }
  }
  return resolved;
}

// --- Send queue serialization (Baileys hangs on overlapping sends) ---
let _sendQueue = Promise.resolve();
function enqueueSend(fn) {
  const task = _sendQueue.then(() => fn(), () => fn());
  _sendQueue = task.catch(() => {});
  return task;
}
function sendWithTimeout(chatId, payload, options = {}, timeoutMs = SEND_TIMEOUT_MS) {
  let timer;
  const timeoutPromise = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(`sendMessage timed out after ${timeoutMs / 1000}s`)), timeoutMs);
  });
  return enqueueSend(() =>
    Promise.race([sock.sendMessage(chatId, payload, options), timeoutPromise]).finally(() => clearTimeout(timer))
  );
}

// --- Baileys socket ---
async function startSocket() {
  const { state, saveCreds } = await useMultiFileAuthState(SESSION_DIR);
  const { version } = await fetchLatestBaileysVersion();

  sock = makeWASocket({
    version,
    auth: state,
    logger,
    printQRInTerminal: false,
    browser: ['agent8088', 'Chrome', '120.0'],
    syncFullHistory: false,
    markOnlineOnConnect: false,
    getMessage: async () => ({ conversation: '' }),
  });

  sock.ev.on('creds.update', () => { saveCreds(); reloadLidMap(); });

  sock.ev.on('connection.update', (update) => {
    const { connection, lastDisconnect, qr } = update;
    if (qr) {
      if (PAIR_ONLY) {
        console.log(JSON.stringify({ ts: Date.now(), event: 'qr', qr }));
      }
      console.log('\n📱 Scan this QR code with WhatsApp → Settings → Linked Devices → Link a Device:\n');
      qrcode.generate(qr, { small: true });
    }
    if (connection === 'close') {
      const reason = new Boom(lastDisconnect?.error)?.output?.statusCode;
      console.error(`Connection closed. Reason: ${reason}`);
      if (reason === DisconnectReason.loggedOut) {
        console.error('Logged out. Delete session dir and re-pair.');
        process.exit(1);
      } else if (reason === 515) {
        console.log('Restart requested (515). Reconnecting in 1s...');
        setTimeout(() => startSocket(), 1000);
      } else {
        console.log('Reconnecting in 3s...');
        setTimeout(() => startSocket(), 3000);
      }
    } else if (connection === 'open') {
      connectionState = 'connected';
      reloadLidMap();
      console.log(`WhatsApp connected as ${sock.user?.id || 'unknown'} (mode: ${WHATSAPP_MODE})`);
      if (PAIR_ONLY) {
        setTimeout(() => process.exit(0), 2000);
      }
    }
  });

  sock.ev.on('messages.upsert', ({ messages, type }) => {
    if (type !== 'notify' && type !== 'append') return;
    for (const msg of messages) {
      try {
        handleInboundMessage(msg);
      } catch (err) {
        console.error('Error handling inbound message:', err);
      }
    }
  });

  sock.ev.on('messages.set', () => {});
}

// --- Normalize WhatsApp JID to bare digits ---
function normalizeWhatsAppId(value) {
  if (!value) return '';
  return String(value).replace(/:.*@/, '@').replace(/@.*/, '').replace(/^\+/, '');
}

// --- Inbound message handler (text-only, LID-resolved, mode-aware) ---
function handleInboundMessage(msg) {
  const rawChatId = msg.key.remoteJid;
  if (!rawChatId) return;

  // Skip broadcast/status messages
  if (rawChatId.endsWith('@broadcast') || rawChatId.endsWith('@newsletter')) return;

  const messageId = msg.key.id;
  const fromMe = msg.key.fromMe;
  const rawSenderId = msg.participant || rawChatId;

  // Echo prevention: skip messages we just sent
  if (fromMe && recentlySentIds.has(messageId)) {
    recentlySentIds.delete(messageId);
    return;
  }

  // --- Self-chat mode: only accept messages from your own account ---
  if (WHATSAPP_MODE === 'self-chat') {
    const myNumber = normalizeWhatsAppId(sock.user?.id);
    const myLid = normalizeWhatsAppId(sock.user?.lid);
    const chatNumber = normalizeWhatsAppId(rawChatId);

    const isSelfChat = (myNumber && chatNumber === myNumber) || (myLid && chatNumber === myLid);

    if (!isSelfChat) {
      return;
    }
  }
  // In bot mode: accept all messages (Python allowlist gates access)

  // --- Extract text (text-only v1) ---
  const content = msg.message;
  if (!content) return;

  let body = null;
  if (content.conversation) {
    body = content.conversation;
  } else if (content.extendedTextMessage) {
    body = content.extendedTextMessage.text;
  } else {
    return; // Media — skip in v1
  }

  if (!body || !body.trim()) return;

  // --- LID → phone resolution ---
  // Resolve both chatId and senderId to phone numbers using the mapping files.
  // The Python adapter uses resolvedSenderId for allowlist matching against
  // human-readable phone numbers in config (e.g. whatsapp_allowed_users=+923214567891).
  const resolvedChatId = resolveToPhone(rawChatId);
  const resolvedSenderId = resolveToPhone(rawSenderId);

  // Get sender name
  let senderName = msg.pushName || '';
  if (!senderName && msg.participant) {
    senderName = msg.participant.split('@')[0];
  }

  const event = {
    messageId,
    chatId: rawChatId,          // original JID (for sending replies back)
    senderId: rawSenderId,      // original sender (for debugging)
    resolvedChatId,             // phone-number form (for allowlist + session key)
    resolvedSenderId,           // phone-number form (for allowlist matching)
    senderName,
    body,
    timestamp: msg.messageTimestamp || Math.floor(Date.now() / 1000),
    fromMe,
  };

  messageQueue.push(event);
  if (messageQueue.length > MAX_QUEUE_SIZE) messageQueue.shift();
}

// --- Express HTTP server ---
const app = express();
app.use(express.json({ limit: '1mb' }));

// Host-header validation (DNS rebinding defense)
app.use((req, res, next) => {
  const host = req.headers.host || '';
  const allowed = ['localhost', '127.0.0.1', '[::1]', '::1'];
  const hostClean = host.replace(/:\d+$/, '');
  if (!allowed.includes(hostClean)) {
    return res.status(403).json({ error: 'forbidden' });
  }
  next();
});

// GET /health
app.get('/health', (req, res) => {
  res.json({
    status: connectionState,
    queueLength: messageQueue.length,
    uptime: Math.floor((Date.now() - startTime) / 1000),
    scriptHash,
  });
});

// GET /messages — drain the queue
app.get('/messages', (req, res) => {
  const messages = messageQueue.splice(0);
  res.json(messages);
});

// POST /send — {chatId, message, replyTo?}
app.post('/send', async (req, res) => {
  const { chatId, message, replyTo } = req.body || {};
  if (!chatId || !message) {
    return res.status(400).json({ error: 'chatId and message required' });
  }
  if (connectionState !== 'connected') {
    return res.status(503).json({ error: 'not connected' });
  }

  try {
    const chunks = [];
    let remaining = message;
    while (remaining.length > MAX_MESSAGE_LENGTH) {
      chunks.push(remaining.slice(0, MAX_MESSAGE_LENGTH));
      remaining = remaining.slice(MAX_MESSAGE_LENGTH);
    }
    chunks.push(remaining);

    let lastMessageId = null;
    for (let i = 0; i < chunks.length; i++) {
      const payload = { text: chunks[i] };
      const options = {};
      if (replyTo && i === 0) {
        options.quoted = { key: { id: replyTo, remoteJid: chatId } };
      }
      const result = await sendWithTimeout(chatId, payload, options);
      lastMessageId = result?.key?.id || null;
      if (lastMessageId) trackSentId(lastMessageId);
      if (i < chunks.length - 1) await new Promise(r => setTimeout(r, CHUNK_DELAY_MS));
    }

    res.json({ success: true, messageId: lastMessageId });
  } catch (err) {
    console.error('Send error:', err);
    res.status(500).json({ error: err.message });
  }
});

// POST /edit — {chatId, messageId, message}
app.post('/edit', async (req, res) => {
  const { chatId, messageId, message } = req.body || {};
  if (!chatId || !messageId || !message) {
    return res.status(400).json({ error: 'chatId, messageId, and message required' });
  }
  if (connectionState !== 'connected') {
    return res.status(503).json({ error: 'not connected' });
  }

  try {
    const text = message.slice(0, MAX_MESSAGE_LENGTH);
    await sendWithTimeout(chatId, {
      text,
      edit: { key: { id: messageId, remoteJid: chatId } },
    });
    res.json({ success: true });
  } catch (err) {
    console.error('Edit error:', err);
    res.status(500).json({ error: err.message });
  }
});

// --- Start ---
if (PAIR_ONLY) {
  console.log(`Pairing mode. Session dir: ${SESSION_DIR}`);
  fs.mkdirSync(SESSION_DIR, { recursive: true });
  startSocket();
} else {
  fs.mkdirSync(SESSION_DIR, { recursive: true });
  reloadLidMap();
  app.listen(PORT, '127.0.0.1', () => {
    console.log(`WhatsApp bridge listening on http://127.0.0.1:${PORT} (mode: ${WHATSAPP_MODE})`);
  });
  startSocket();
}