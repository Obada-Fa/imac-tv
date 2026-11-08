import 'dart:convert';
import 'dart:io';
import 'package:flutter_test/flutter_test.dart';
import 'package:nexus_tv/discovery.dart';
import 'package:nexus_tv/tv_store.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

void main() {
  group('links', () {
    test('the TV QR gives its origin and code', () {
      final link = parseLink('http://192.168.1.20:8765/?pair=123456')!;
      expect(link.origin, Uri.parse('http://192.168.1.20:8765'));
      expect(link.code, '123456');
    });

    test('the app link gives its origin and code', () {
      final link = parseLink('nexustv://pair?host=192.168.1.20:8765&code=654321')!;
      expect(link.origin, Uri.parse('http://192.168.1.20:8765'));
      expect(link.code, '654321');
    });

    test('a bad code is dropped, not the TV', () {
      final link = parseLink('http://10.0.0.5:8765/?pair=12ab')!;
      expect(link.origin.host, '10.0.0.5');
      expect(link.code, isNull);
    });

    test('other QR codes are not TVs', () {
      expect(parseLink('https://example.com/some/page'), isNull);
      expect(parseLink('WIFI:S:home;T:WPA;P:secret;;'), isNull);
      expect(parseLink('nexustv://other?host=1.2.3.4'), isNull);
      expect(parseLink('nexustv://pair'), isNull);
    });

    test('only addresses on this network may be opened from a link', () {
      for (final host in ['192.168.1.20', '10.1.2.3', '172.20.0.4', 'zorin-tv', 'tv.local', '100.80.1.2']) {
        expect(isLocalHost(host), true, reason: host);
      }
      for (final host in ['8.8.8.8', 'example.com', '172.32.0.1', 'evil.com.attacker.net', '999.1.1.1']) {
        expect(isLocalHost(host), false, reason: host);
      }
    });

    test('addresses read like the TV shows them', () {
      expect(parseOrigin('192.168.1.20'), Uri.parse('http://192.168.1.20:8765'));
      expect(displayAddress(parseOrigin('192.168.1.20')), '192.168.1.20');
      expect(displayAddress(parseOrigin('192.168.1.20:9000')), '192.168.1.20:9000');
      expect(() => parseOrigin('http://x/y?z'), throwsFormatException);
    });
  });

  group('store', () {
    setUpAll(() {
      sqfliteFfiInit();
      databaseFactory = databaseFactoryFfi;
    });

    late Directory dir;
    setUp(() => dir = Directory.systemTemp.createTempSync('nexus-tv-store'));
    tearDown(() => dir.deleteSync(recursive: true));

    test('the old app\'s TV address carries over', () async {
      final path = '${dir.path}/db.sqlite';
      final old = await openDatabase(path);
      await old.execute('CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)');
      await old.insert('settings', {'key': 'imac_address', 'value': '192.168.68.50:8765'});
      await old.close();

      final store = TvStore(databasePath: path);
      await store.load();
      expect(store.last?.origin, Uri.parse('http://192.168.68.50:8765'));
    });

    test('the most recently used TV comes first and survives a restart', () async {
      final path = '${dir.path}/db.sqlite';
      final store = TvStore(databasePath: path);
      await store.load();
      await store.remember(Uri.parse('http://192.168.1.2:8765'), 'Living room');
      await store.remember(Uri.parse('http://192.168.1.3:8765'), 'Bedroom');
      await store.remember(Uri.parse('http://192.168.1.2:8765'), 'Living room');
      expect(store.tvs.map((t) => t.name), ['Living room', 'Bedroom']);

      final again = TvStore(databasePath: path);
      await again.load();
      expect(again.tvs.map((t) => t.name), ['Living room', 'Bedroom']);
      await again.forget(Uri.parse('http://192.168.1.2:8765'));
      expect(again.last?.name, 'Bedroom');
    });
  });

  group('probe', () {
    late HttpServer server;
    setUp(() async => server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0));
    tearDown(() => server.close(force: true));

    Uri origin() => Uri.parse('http://127.0.0.1:${server.port}');

    test('a Nexus TV answers with its name', () async {
      server.listen((r) {
        r.response.write(jsonEncode({'app': 'nexus-tv', 'name': 'zorin-tv'}));
        r.response.close();
      });
      final tv = await probeTv(origin());
      expect(tv?.name, 'zorin-tv');
    });

    test('a TV from before /api/hello still counts', () async {
      server.listen((r) {
        r.response.statusCode = 404;
        r.response.write(jsonEncode({'error': 'Not found'}));
        r.response.close();
      });
      final tv = await probeTv(origin());
      expect(tv?.name, 'Nexus TV');
    });

    test('something else on the port is not a TV', () async {
      server.listen((r) {
        r.response.write('<html>router</html>');
        r.response.close();
      });
      final tv = await probeTv(origin());
      expect(tv, isNull);
    });
  });

  group('discovery', () {
    test('asks every other address on each network', () {
      final list = candidates({'192.168.1.': '192.168.1.7'});
      expect(list.length, 253);
      expect(list.map((u) => u.host), isNot(contains('192.168.1.7')));
      expect(list.first, Uri.parse('http://192.168.1.1:8765'));
    });

    test('reports the TVs that answer', () async {
      final found = await discoverTvs(
        prefixes: () async => {'10.0.0.': '10.0.0.9'},
        probe: (origin, {timeout = Duration.zero}) async =>
            origin.host == '10.0.0.40' ? TvInfo(origin: origin, name: 'TV') : null,
      ).toList();
      expect(found.map((t) => t.origin.host), ['10.0.0.40']);
    });
  });
}
