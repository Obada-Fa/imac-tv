// Which network the phones find "Nexus TV" on, and how it answers them (SSDP, the DIAL discovery step).
//
// Left alone, yt-cast-receiver's SSDP layer (@patrickkfkan/peer-ssdp) joins the discovery group on every
// IPv4 interface, so one phone search arrives once per interface, and each copy is answered once per
// interface: N x N replies, each naming a different address. On a laptop running Docker (or Tailscale,
// libvirt, a VPN) many of those replies name an address the phone can't reach (172.17.0.1, 100.x) and come
// from it; a phone that keeps the first reply per device never lists the TV. Here we:
//   - advertise only on physical interfaces (those with /sys/class/net/<name>/device: Wi-Fi, Ethernet,
//     USB tethering), never docker0/br-*/veth*/virbr*/tailscale0/wg*/tun*;
//   - answer each search once, from and naming the TV's address on the phone's own subnet;
//   - repeat the "alive" announcement every ALIVE_MS (the library sends it once, maybe before Wi-Fi is up).
// IMACTV_MUSIC_IFACES=wlp2s0,eno1 overrides the interface list.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import dgram from 'node:dgram';
import { createRequire } from 'node:module';

const VIRTUAL = /^(lo|docker|br-|veth|virbr|vnet|tailscale|zt|tun|tap|wg|lxc|lxd|cni|flannel|cali|vmnet|vboxnet|kube|podman|ham|nordlynx|proton|ppp)/;
export const ALIVE_MS = 60000;
const DUP_MS = 1500;

export function lanInterfaces(sysRoot = '/sys/class/net', env = process.env) {
  const forced = (env.IMACTV_MUSIC_IFACES || '').split(',').map((s) => s.trim()).filter(Boolean);
  if (forced.length) return forced;
  let names = [];
  try { names = fs.readdirSync(sysRoot); } catch { return []; }
  const physical = (n) => fs.existsSync(path.join(sysRoot, n, 'device'));
  const ports = (n) => { try { return fs.readdirSync(path.join(sysRoot, n, 'brif')); } catch { return []; } };
  // A real NIC, a bond, or a bridge someone put a real NIC into (br0 on some setups); Docker/libvirt bridges
  // hold only veth/vnet ports.
  return names.filter((n) => !VIRTUAL.test(n) && (physical(n) || fs.existsSync(path.join(sysRoot, n, 'bonding'))
    || ports(n).some((p) => !VIRTUAL.test(p) && physical(p)))).sort();
}

const toInt = (ip) => ip.split('.').reduce((a, b) => ((a << 8) | (Number(b) & 255)) >>> 0, 0);

// The TV's IPv4 address on the same subnet as `remote`, among `names` (all non-virtual ones when empty).
export function localAddressFor(remote, names = [], ifaces = os.networkInterfaces()) {
  if (!/^\d+\.\d+\.\d+\.\d+$/.test(remote || '')) return null;
  const r = toInt(remote);
  for (const [name, addrs] of Object.entries(ifaces)) {
    if (names.length ? !names.includes(name) : VIRTUAL.test(name)) continue;
    for (const a of addrs || []) {
      if (a.family !== 'IPv4' && a.family !== 4) continue;
      if (a.internal) continue;
      const mask = toInt(a.netmask || '255.255.255.0');
      if (((toInt(a.address) & mask) >>> 0) === ((r & mask) >>> 0)) return a.address;
    }
  }
  return null;
}

// Addresses we can be found on right now, for the TV's Settings and `nexus-tv-cast check`.
export function lanAddresses(names, ifaces = os.networkInterfaces()) {
  const out = [];
  for (const n of names) {
    for (const a of ifaces[n] || []) {
      if ((a.family === 'IPv4' || a.family === 4) && !a.internal) out.push({ iface: n, address: a.address });
    }
  }
  return out;
}

// Searches heard from other devices (phones), newest first: the one fact `nexus-tv-cast check` can't get by
// searching from the TV itself. No entry after the cast menu was opened = the search never reached this PC
// (router keeps Wi-Fi devices apart, guest network, firewall on UDP 1900).
const heard = new Map();
let onHeard = null;
export function setOnHeard(fn) { onHeard = fn; }
export function heardList(max = 5) {
  return [...heard.entries()].reverse().slice(0, max).map(([address, h]) => ({ address, ...h }));   // kept in order heard
}
export function noteSearch(remote, local, names, ifaces = os.networkInterfaces()) {
  if (lanAddresses(names.length ? names : Object.keys(ifaces), ifaces).some((a) => a.address === remote)) return;
  if (/^127\./.test(remote || '')) return;                               // our own check
  const fresh = !heard.has(remote);
  const h = heard.get(remote) || { count: 0 };
  heard.delete(remote);
  heard.set(remote, { at: Date.now() / 1000, count: h.count + 1, answered_from: local || null });
  while (heard.size > 20) heard.delete(heard.keys().next().value);
  if (onHeard) { try { onHeard(fresh); } catch { /* reporting only */ } }
}

function sendFrom(local, args) {
  const [msg, offset, length, port, address, cb] = args;
  const s = dgram.createSocket({ type: 'udp4', reuseAddr: true });
  s.on('error', (e) => { try { s.close(); } catch { /* closed */ } if (typeof cb === 'function') cb(e); });
  s.bind(0, local, () => {
    s.send(msg, offset, length, port, address, (err, bytes) => {
      try { s.close(); } catch { /* closed */ }
      if (typeof cb === 'function') cb(err, bytes);
    });
  });
}

// Patch the SSDP peer's prototype (once, before the receiver starts). `names`: the interfaces we advertise on.
export function patchSsdp(names, log = () => {}, require = createRequire(import.meta.url)) {
  const ssdp = require('@patrickkfkan/peer-ssdp');
  const proto = Object.getPrototypeOf(ssdp.createPeer());
  if (proto.__nexusPatched) return proto;
  proto.__nexusPatched = true;
  const reply = proto.reply;
  const alive = proto.alive;
  const start = proto.start;
  const close = proto.close;

  proto.reply = function (headers, address, callback) {
    const key = `${address.address}:${address.port}|${headers.ST}`;
    const now = Date.now();
    this.__seen = this.__seen || new Map();
    for (const [k, t] of this.__seen) if (now - t > DUP_MS) this.__seen.delete(k);
    if (this.__seen.has(key)) return undefined;        // the same search, received on another socket
    this.__seen.set(key, now);
    const local = localAddressFor(address.address, names);
    noteSearch(address.address, local, names);
    if (!local) return reply.call(this, headers, address, callback);     // unknown subnet: answer the old way
    const real = this.ucSocket;
    this.ucSocket = { send: (make) => sendFrom(local, make(local)) };
    try { return reply.call(this, headers, address, callback); } finally { this.ucSocket = real; }
  };

  proto.alive = function (headers, callback) {
    (this.__alive = this.__alive || []).push({ ...headers });
    return alive.call(this, headers, callback);
  };

  proto.start = function (...args) {
    const out = start.apply(this, args);
    clearInterval(this.__aliveTimer);
    this.__aliveTimer = setInterval(() => {
      for (const h of this.__alive || []) {
        try { alive.call(this, { ...h, DATE: undefined }); } catch (e) { log('ssdp alive failed:', e?.message || e); }
      }
    }, ALIVE_MS);
    if (this.__aliveTimer.unref) this.__aliveTimer.unref();
    return out;
  };

  proto.close = function (...args) {
    clearInterval(this.__aliveTimer);
    return close.apply(this, args);
  };
  return proto;
}
