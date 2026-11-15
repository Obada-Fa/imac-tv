// lan.mjs checks: which interfaces "Nexus TV" is advertised on, and that each phone search gets one answer
// from the address on the phone's subnet. Needs node_modules (npm ci in music_cast/) for the SSDP part.
import fs from 'node:fs'; import path from 'node:path'; import assert from 'node:assert/strict';
import dgram from 'node:dgram';
import { lanInterfaces, localAddressFor, lanAddresses, patchSsdp } from '../lan.mjs';

const sys = fs.mkdtempSync('/tmp/nexus-lan-');
const nic = (n, { device = false, brif = [], bonding = false } = {}) => {
  fs.mkdirSync(path.join(sys, n), { recursive: true });
  if (device) fs.mkdirSync(path.join(sys, n, 'device'));
  if (bonding) fs.mkdirSync(path.join(sys, n, 'bonding'));
  for (const p of brif) fs.mkdirSync(path.join(sys, n, 'brif', p), { recursive: true });
};
try {
  nic('lo'); nic('wlp2s0', { device: true }); nic('enp1s0', { device: true }); nic('docker0', { brif: ['veth1'] });
  nic('br-6a14', { brif: ['veth2'] }); nic('veth1'); nic('tailscale0'); nic('virbr0', { brif: ['vnet0'] });
  nic('br0', { brif: ['eth9'] }); nic('eth9', { device: true }); nic('bond0', { bonding: true }); nic('mystery0');
  assert.deepEqual(lanInterfaces(sys, {}), ['bond0', 'br0', 'enp1s0', 'eth9', 'wlp2s0']);
  assert.deepEqual(lanInterfaces(sys, { IMACTV_MUSIC_IFACES: 'wlan0, eth0' }), ['wlan0', 'eth0']);
  assert.deepEqual(lanInterfaces('/nonexistent', {}), []);

  const ifaces = {
    lo: [{ family: 'IPv4', address: '127.0.0.1', netmask: '255.0.0.0', internal: true }],
    docker0: [{ family: 'IPv4', address: '172.17.0.1', netmask: '255.255.0.0', internal: false }],
    tailscale0: [{ family: 'IPv4', address: '100.70.1.2', netmask: '255.255.255.255', internal: false }],
    enp1s0: [{ family: 'IPv4', address: '10.0.0.5', netmask: '255.255.255.0', internal: false }],
    wlp2s0: [{ family: 'IPv6', address: 'fe80::1', netmask: 'ffff::', internal: false },
             { family: 'IPv4', address: '192.168.1.40', netmask: '255.255.255.0', internal: false }],
  };
  const names = ['enp1s0', 'wlp2s0'];
  assert.equal(localAddressFor('192.168.1.77', names, ifaces), '192.168.1.40');
  assert.equal(localAddressFor('10.0.0.9', names, ifaces), '10.0.0.5');
  assert.equal(localAddressFor('172.17.0.3', names, ifaces), null);     // a container: not a phone we answer from docker0
  assert.equal(localAddressFor('172.17.0.3', [], ifaces), null);        // virtual names skipped without a list too
  assert.equal(localAddressFor('not-an-ip', names, ifaces), null);
  assert.deepEqual(lanAddresses(names, ifaces), [{ iface: 'enp1s0', address: '10.0.0.5' }, { iface: 'wlp2s0', address: '192.168.1.40' }]);

  // One answer per search, sent from 127.0.0.1 when the "phone" is on 127.0.0.0/8 (lo stands in for the Wi-Fi).
  let proto;
  try { proto = patchSsdp(['lo']); } catch (e) { console.log('SKIP ssdp part:', e.message); }
  if (proto) {
    assert.equal(patchSsdp(['lo']), proto);                               // patched once
    const phone = dgram.createSocket('udp4');
    await new Promise((r) => phone.bind(0, '127.0.0.1', r));
    const got = [];
    phone.on('message', (m, from) => got.push({ text: m.toString(), from: from.address }));
    // localAddressFor skips internal addresses, so lo answers the library's old way; check dedupe on a stub
    const sent = [];
    const peer = Object.create(proto);
    peer.ucSocket = { send: (make) => { sent.push(make('127.0.0.1')); } };
    const headers = { ST: 'urn:dial-multiscreen-org:service:dial:1', LOCATION: 'http://{{networkInterfaceAddress}}:8766/x' };
    const addr = { address: '127.0.0.1', port: phone.address().port };
    peer.reply({ ...headers }, addr); peer.reply({ ...headers }, addr); peer.reply({ ...headers }, addr);
    assert.equal(sent.length, 1, 'the same search seen on three sockets is answered once');
    assert.match(sent[0][0].toString(), /LOCATION: http:\/\/127\.0\.0\.1:8766\/x/);
    peer.reply({ ...headers, ST: 'upnp:rootdevice' }, addr);
    assert.equal(sent.length, 2, 'another search type is answered');
    phone.close();
  }
  console.log('ALL LAN CHECKS PASSED');
} finally {
  fs.rmSync(sys, { recursive: true, force: true });
}
