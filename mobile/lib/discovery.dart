import 'dart:async';
import 'dart:io';
import 'tv_store.dart';

/// The /24 networks this phone is on, as "192.168.1." prefixes, with the
/// phone's own address in each. Mobile data and VPN addresses are skipped.
Future<Map<String, String>> localPrefixes() async {
  final prefixes = <String, String>{};
  try {
    final interfaces = await NetworkInterface.list(type: InternetAddressType.IPv4);
    for (final iface in interfaces) {
      for (final addr in iface.addresses) {
        final p = addr.address.split('.');
        if (p.length != 4) continue;
        final a = int.tryParse(p[0]) ?? 0, b = int.tryParse(p[1]) ?? 0;
        final private = a == 10 || (a == 172 && b >= 16 && b <= 31) || (a == 192 && b == 168);
        if (private) prefixes.putIfAbsent('${p[0]}.${p[1]}.${p[2]}.', () => addr.address);
      }
    }
  } catch (_) {}
  return prefixes;
}

/// Every address worth asking on the given networks: .1 to .254, not the phone itself.
List<Uri> candidates(Map<String, String> prefixes, {int port = tvPort}) => [
      for (final entry in prefixes.entries)
        for (var i = 1; i < 255; i++)
          if ('${entry.key}$i' != entry.value) Uri(scheme: 'http', host: '${entry.key}$i', port: port),
    ];

/// Ask every address on this Wi-Fi whether it is a Nexus TV; TVs arrive as they answer.
/// 64 at a time with a short timeout: a /24 takes about three seconds.
Stream<TvInfo> discoverTvs({
  Future<Map<String, String>> Function() prefixes = localPrefixes,
  Future<TvInfo?> Function(Uri origin, {Duration timeout}) probe = probeTv,
  int parallel = 64,
  Duration timeout = const Duration(milliseconds: 900),
}) {
  final out = StreamController<TvInfo>();
  var cancelled = false;
  out.onCancel = () => cancelled = true;
  () async {
    final queue = candidates(await prefixes());
    var next = 0;
    Future<void> worker() async {
      while (!cancelled && next < queue.length) {
        final origin = queue[next++];
        final tv = await probe(origin, timeout: timeout);
        if (tv != null && !cancelled) out.add(tv);
      }
    }

    await Future.wait([for (var i = 0; i < parallel; i++) worker()]);
    if (!out.isClosed) await out.close();
  }();
  return out.stream;
}
