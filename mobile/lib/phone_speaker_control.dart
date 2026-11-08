import 'dart:async';
import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';

/// Plays the paired TV's audio on this phone. While on, the TV makes the phone
/// its default output (so Discord is heard here). Android owns the stream
/// (foreground service), so it keeps playing with the screen off.
class PhoneSpeakerControl extends ChangeNotifier {
  static const channel = MethodChannel('nexus_remote/phone_speaker');
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

  Future<void> start({required Uri origin, required String token}) async {
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
      await channel.invokeMethod<void>('start', {
        'origin': origin.origin,
        'cookie': 'imactv=$token',
      });
      enabled = true;
    } on PlatformException catch (e) {
      error = e.message ?? 'Could not start the phone speaker';
    } on MissingPluginException {
      error = 'Phone speaker requires the Android app.';
    } finally {
      busy = false;
      if (!_disposed) notifyListeners();
    }
  }

  Future<void> stop() async {
    if (!supported) return;
    try {
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
