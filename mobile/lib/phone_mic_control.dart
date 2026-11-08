import 'dart:async';
import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';

/// Shares this phone's microphone with the paired TV, which makes it the
/// default input while connected. Android owns the stream (foreground service),
/// so it keeps running with the screen off.
class PhoneMicControl extends ChangeNotifier {
  static const channel = MethodChannel('nexus_remote/phone_mic');
  bool get supported =>
      !kIsWeb && defaultTargetPlatform == TargetPlatform.android;
  bool enabled = false;
  bool busy = false;
  String? error;
  bool _disposed = false;

  Future<void> refresh() async {
    if (!supported || _disposed) return;
    try {
      final state = await channel.invokeMapMethod<String, dynamic>('status');
      enabled = state?['enabled'] == true;
      error = state?['error'] as String?;
      notifyListeners();
    } on MissingPluginException {
      // Widget tests and platforms without the Android bridge.
    }
  }

  /// Start on connect unless the user turned the mic off in this app.
  Future<void> autoStart({required Uri origin, required String token}) async {
    if (!supported || enabled || busy) return;
    try {
      if (await channel.invokeMethod<bool>('wanted') == false) return;
    } on MissingPluginException {
      return;
    }
    await start(origin: origin, token: token, remember: false);
  }

  Future<void> start({
    required Uri origin,
    required String token,
    bool remember = true,
  }) async {
    if (!supported || busy) return;
    if (token.isEmpty) {
      error = 'Pair with the TV using its six-digit code first.';
      notifyListeners();
      return;
    }
    busy = true;
    error = null;
    notifyListeners();
    try {
      if (remember) {
        await channel.invokeMethod<void>('setWanted', {'wanted': true});
      }
      await channel.invokeMethod<void>('start', {
        'origin': origin.origin,
        'cookie': 'imactv=$token',
      });
      enabled = true;
    } on PlatformException catch (e) {
      error = e.message ?? 'Could not share the microphone';
    } on MissingPluginException {
      error = 'Microphone sharing requires the Android app.';
    } finally {
      busy = false;
      if (!_disposed) notifyListeners();
    }
  }

  Future<void> stop() async {
    if (!supported) return;
    try {
      await channel.invokeMethod<void>('setWanted', {'wanted': false});
      await channel.invokeMethod<void>('stop');
    } on MissingPluginException {
      // No native stream can exist without the bridge.
    }
    enabled = false;
    busy = false;
    error = null;
    if (!_disposed) notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    super.dispose();
  }
}
