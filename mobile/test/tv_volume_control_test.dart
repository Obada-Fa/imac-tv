import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:nexus_tv/tv_volume_control.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  late List<MethodCall> calls;
  var nativeEnabled = false;

  setUp(() {
    calls = [];
    nativeEnabled = false;
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(TvVolumeControl.channel, (call) async {
          calls.add(call);
          if (call.method == 'start') nativeEnabled = true;
          if (call.method == 'stop') nativeEnabled = false;
          return call.method == 'status' ? {'enabled': nativeEnabled} : null;
        });
  });
  tearDown(() {
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(TvVolumeControl.channel, null);
  });

  testWidgets('Android bridge uses paired origin and survives UI suspension', (
    tester,
  ) async {
    final control = TvVolumeControl();
    await control.initialize();
    await control.start(
      origin: Uri.parse('http://tv.test:8765'),
      token: 'paired-token',
    );
    expect(control.enabled, true);
    final start = calls.singleWhere((call) => call.method == 'start');
    expect(start.arguments, {
      'origin': 'http://tv.test:8765',
      'cookie': 'imactv=paired-token',
    });
    control.didChangeAppLifecycleState(AppLifecycleState.paused);
    expect(calls.where((call) => call.method == 'stop'), isEmpty);
    // A notification stop is reflected when the app resumes.
    nativeEnabled = false;
    control.didChangeAppLifecycleState(AppLifecycleState.resumed);
    await tester.pump();
    expect(control.enabled, false);
    control.dispose();
  });

  testWidgets(
    'iPhone presents on-screen controls guidance without native calls',
    (tester) async {
      final control = TvVolumeControl();
      await control.initialize();
      expect(control.supported, false);
      expect(calls, isEmpty);
      control.dispose();
    },
    variant: TargetPlatformVariant.only(TargetPlatform.iOS),
  );

  testWidgets(
    'denied notification permission leaves mode off with a useful error',
    (tester) async {
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
          .setMockMethodCallHandler(TvVolumeControl.channel, (_) async {
            throw PlatformException(
              code: 'notifications_denied',
              message: 'Allow notifications',
            );
          });
      final control = TvVolumeControl();
      await control.start(
        origin: Uri.parse('http://tv.test:8765'),
        token: 'paired-token',
      );
      expect(control.enabled, false);
      expect(control.error, 'Allow notifications');
      expect(control.busy, false);
      control.dispose();
    },
  );
}
