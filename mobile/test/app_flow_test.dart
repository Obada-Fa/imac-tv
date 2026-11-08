import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:nexus_tv/main.dart';
import 'package:nexus_tv/native.dart';
import 'package:nexus_tv/tv_store.dart';

class _Store extends TvStore {
  _Store(this.saved);
  final List<(String, String)> saved;
  @override
  Future<void> load() async {
    for (final (address, name) in saved.reversed) {
      await remember(parseOrigin(address), name);
    }
  }
}

class _Links extends TvLinks {
  _Links([this.first]);
  final String? first;
  int scans = 0;
  @override
  Future<String?> initial() async => first;
  @override
  Future<String?> scanQr() async {
    scans++;
    return null;
  }
}

Stream<TvInfo> _found() => Stream.fromIterable([
      TvInfo(origin: parseOrigin('192.168.1.40'), name: 'Bedroom TV'),
    ]);

void main() {
  Future<_Links> start(WidgetTester tester, List<(String, String)> saved, {String? link}) async {
    final links = _Links(link);
    // A tall phone, so the whole connect page is built.
    tester.view.physicalSize = const Size(1080, 3000);
    tester.view.devicePixelRatio = 2.5;
    addTearDown(tester.view.reset);
    await tester.pumpWidget(NexusTvApp(
      store: _Store(saved),
      controls: NativeControls(),
      links: links,
      discover: _found,
    ));
    // flutter_test answers every HTTP request with 400: no TV is reachable.
    await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 200)));
    await tester.pumpAndSettle();
    return links;
  }

  testWidgets('without a TV, the phone lists TVs on its Wi-Fi', (tester) async {
    final links = await start(tester, []);
    expect(find.text('Connect to your TV'), findsOneWidget);
    expect(find.text('Bedroom TV'), findsOneWidget);
    expect(find.text('192.168.1.40'), findsOneWidget);
    await tester.tap(find.text('Scan the QR code on the TV'));
    await tester.pump();
    expect(links.scans, 1);
  });

  testWidgets('an unreachable TV offers another TV', (tester) async {
    await start(tester, [('192.168.1.20', 'Living room'), ('192.168.1.30', 'Kitchen')]);
    expect(find.text("Can't reach Living room"), findsOneWidget);
    expect(find.text('Try again'), findsOneWidget);
    expect(find.text('Not reachable'), findsOneWidget);
    expect(find.text('Kitchen'), findsOneWidget);
    expect(find.text('Bedroom TV'), findsOneWidget);
    expect(find.text('Enter address'), findsOneWidget);
  });

  testWidgets('a QR code that is not a TV says so', (tester) async {
    await start(tester, [], link: 'https://example.com/menu');
    expect(find.text("That isn't a Nexus TV QR code."), findsOneWidget);
    expect(find.text('Connect to your TV'), findsOneWidget);
  });

  testWidgets('a link to a TV elsewhere on the internet is refused', (tester) async {
    await start(tester, [], link: 'nexustv://pair?host=8.8.8.8:8765&code=123456');
    expect(find.text("That isn't a Nexus TV QR code."), findsOneWidget);
  });

  testWidgets('entering an address opens that TV', (tester) async {
    await start(tester, []);
    await tester.tap(find.text('Enter address'));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField), 'not a/valid?address');
    await tester.tap(find.text('Connect'));
    await tester.pumpAndSettle();
    expect(find.textContaining('for example'), findsOneWidget);
    await tester.enterText(find.byType(TextField), '192.168.1.77');
    await tester.tap(find.text('Connect'));
    await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 200)));
    await tester.pumpAndSettle();
    // It isn't there either, so the phone says which address failed.
    expect(find.text("Can't reach 192.168.1.77"), findsOneWidget);
  });
}
