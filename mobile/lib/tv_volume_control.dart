import 'dart:async';
import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:flutter/widgets.dart';

/// Owned by the TV connection, so changing tabs does not end the session.
/// Android owns all background networking; no Dart background isolate is needed.
class TvVolumeControl extends ChangeNotifier with WidgetsBindingObserver {
  static const channel = MethodChannel('nexus_remote/tv_volume');
  bool get supported =>
      !kIsWeb && defaultTargetPlatform == TargetPlatform.android;
  bool enabled = false;
  bool busy = false;
  String? error;
  Timer? _timer;
  bool _disposed = false;
  bool _observing = false;
  int _generation = 0;

  Future<void> initialize() async {
    if (!supported) return;
    if (!_observing) {
      WidgetsBinding.instance.addObserver(this);
      _observing = true;
    }
    await refresh();
  }

  Future<void> refresh() async {
    if (!supported || _disposed || busy) return;
    final generation = _generation;
    try {
      final state = await channel.invokeMapMethod<String, dynamic>('status');
      if (_disposed || generation != _generation || busy) return;
      enabled = state?['enabled'] == true;
      error = state?['error'] as String?;
      _scheduleRefresh();
      notifyListeners();
    } on MissingPluginException {
      // Widget tests and platforms without the Android bridge.
    } on PlatformException catch (e) {
      if (!_disposed) {
        error = e.message ?? 'Could not read TV volume controls';
        notifyListeners();
      }
    }
  }

  void _scheduleRefresh() {
    _timer?.cancel();
    if (enabled &&
        WidgetsBinding.instance.lifecycleState != AppLifecycleState.paused) {
      _timer = Timer(const Duration(seconds: 2), refresh);
    }
  }

  Future<void> start({required Uri origin, required String token}) async {
    if (!supported || busy) return;
    if (token.isEmpty) {
      throw StateError('Pair with the TV using its six-digit code first.');
    }
    busy = true;
    final generation = ++_generation;
    error = null;
    notifyListeners();
    try {
      await channel.invokeMethod<void>('start', {
        'origin': origin.origin,
        'cookie': 'imactv=$token',
      });
      if (!_disposed && generation == _generation) enabled = true;
    } on PlatformException catch (e) {
      if (generation == _generation) {
        error = e.message ?? 'Could not enable TV volume buttons';
      }
    } on MissingPluginException {
      if (generation == _generation) {
        error = 'TV volume buttons require the Android app.';
      }
    } finally {
      if (!_disposed && generation == _generation) {
        busy = false;
        _scheduleRefresh();
        notifyListeners();
      }
    }
  }

  Future<void> stop() async {
    if (!supported) return;
    ++_generation;
    _timer?.cancel();
    // Also stop a session left running when Flutter was recreated.
    try {
      await channel.invokeMethod<void>('stop');
    } on MissingPluginException {
      // No native session can exist without the bridge.
    }
    if (_disposed) return;
    enabled = false;
    busy = false;
    error = null;
    notifyListeners();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) {
      unawaited(refresh());
    } else if (state == AppLifecycleState.paused) {
      _timer?.cancel();
    }
  }

  @override
  void dispose() {
    _disposed = true;
    _timer?.cancel();
    if (_observing) WidgetsBinding.instance.removeObserver(this);
    // Leaving the Flutter UI must not stop the native background session.
    super.dispose();
  }
}
