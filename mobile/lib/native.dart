import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'phone_mic_control.dart';
import 'phone_speaker_control.dart';
import 'scan_page.dart';
import 'tv_volume_control.dart';

/// The Android extras around the TV's web remote: hardware volume buttons,
/// phone microphone and phone speaker. Each is bound to one TV at a time.
class NativeControls {
  final hardwareVolume = TvVolumeControl();
  final phoneMic = PhoneMicControl();
  final phoneSpeaker = PhoneSpeakerControl();

  Future<void> initialize() => hardwareVolume.initialize();

  /// Before switching TVs: nothing may keep talking to the old one.
  Future<void> stopAll() async {
    await Future.wait([
      hardwareVolume.stop().catchError((_) {}),
      phoneMic.stop().catchError((_) {}),
      phoneSpeaker.stop().catchError((_) {}),
    ]);
  }
}

/// nexustv:// links (from the TV's QR page) and the QR scanner.
class TvLinks {
  TvLinks() {
    channel.setMethodCallHandler((call) async {
      if (call.method == 'link' && call.arguments is String) {
        _links.add(call.arguments as String);
      }
    });
  }

  static const channel = MethodChannel('nexus_tv/link');
  final _links = StreamController<String>.broadcast();

  /// Links that arrive while the app is open.
  Stream<String> get links => _links.stream;

  /// The link that started the app, once.
  Future<String?> initial() async {
    try {
      return await channel.invokeMethod<String>('initialLink');
    } catch (_) {
      return null;
    }
  }

  /// Opens the app's own camera scanner; the scanned text, or null if the
  /// user went back.
  Future<String?> scanQr(NavigatorState navigator) =>
      navigator.push<String>(MaterialPageRoute(builder: (_) => const ScanPage()));
}
