import 'dart:async';
import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:webview_flutter/webview_flutter.dart';
import 'package:webview_flutter_android/webview_flutter_android.dart';
import 'native.dart';
import 'tv_store.dart';
import 'tv_volume_control.dart';

/// Display the TV's actual phone UI. Menus, playback, catalog and styling all
/// come from that one source, including future TV updates.
class TvWebPage extends StatefulWidget {
  const TvWebPage({
    super.key,
    required this.controls,
    required this.origin,
    required this.onConnected,
    required this.onUnreachable,
    required this.onSwitch,
    required this.onExit,
    this.pairCode,
    this.name = 'your TV',
  });
  final NativeControls controls;
  final Uri origin;

  /// From the TV's QR: the TV's page pairs itself with it.
  final String? pairCode;
  final String name;
  final void Function(TvInfo tv) onConnected;
  final VoidCallback onUnreachable;
  final VoidCallback onSwitch;
  final VoidCallback onExit;
  @override
  State<TvWebPage> createState() => _TvWebPageState();
}

class _TvWebPageState extends State<TvWebPage> {
  bool _sameOrigin(String url, Uri origin) {
    final uri = Uri.tryParse(url);
    return uri != null &&
        {'http', 'https'}.contains(uri.scheme) &&
        uri.origin == origin.origin;
  }

  WebViewController? _web;
  Uri? _origin;
  bool _ready = false;
  bool _left = false;

  NativeControls get _controls => widget.controls;

  @override
  void initState() {
    super.initState();
    _controls.hardwareVolume.addListener(_syncNativeState);
    _controls.phoneMic.addListener(_syncNativeState);
    _controls.phoneSpeaker.addListener(_syncNativeState);
    unawaited(_open());
  }

  /// Leave for the connect page once: the WebView may report several errors.
  void _unreachable() {
    if (_left || !mounted) return;
    _left = true;
    widget.onUnreachable();
  }

  Future<void> _open() async {
    final origin = widget.origin;
    // A quick check first: a WebView takes half a minute to give up on a TV that is off.
    final tv = await probeTv(origin);
    if (!mounted) return;
    if (tv == null) return _unreachable();
    widget.onConnected(tv);
    _origin = origin;
    try {
      final web = WebViewController();
      await web.setJavaScriptMode(JavaScriptMode.unrestricted);
      await web.setBackgroundColor(const Color(0xFF0B111D));
      await web.addJavaScriptChannel(
        'NexusTv',
        onMessageReceived: (message) {
          if (_origin == origin) unawaited(_message(message.message));
        },
      );
      await web.setNavigationDelegate(
        NavigationDelegate(
          onNavigationRequest: (request) => _sameOrigin(request.url, origin)
              ? NavigationDecision.navigate
              : NavigationDecision.prevent,
          onPageStarted: (_) {
            if (mounted) setState(() => _ready = false);
          },
          onPageFinished: (url) async {
            if (!mounted || !_sameOrigin(url, origin)) return;
            try {
              await web.runJavaScript(
                await rootBundle.loadString('assets/tv-native.js'),
              );
              if (!mounted) return;
              setState(() => _ready = true);
              _syncNativeState();
              unawaited(_autoShareMic(origin));
            } catch (_) {
              _unreachable();
            }
          },
          onWebResourceError: (error) {
            if (error.isForMainFrame == true) _unreachable();
          },
        ),
      );
      await web.setOnJavaScriptConfirmDialog((request) async {
        if (!mounted) return false;
        return await showDialog<bool>(
              context: context,
              builder: (context) => AlertDialog(
                content: Text(request.message),
                actions: [
                  TextButton(
                    onPressed: () => Navigator.pop(context, false),
                    child: const Text('Cancel'),
                  ),
                  TextButton(
                    onPressed: () => Navigator.pop(context, true),
                    child: const Text('Continue'),
                  ),
                ],
              ),
            ) ??
            false;
      });
      if (web.platform is AndroidWebViewController) {
        await (web.platform as AndroidWebViewController).setOnShowFileSelector(
          (_) async =>
              await TvVolumeControl.channel.invokeListMethod<String>(
                'pickImage',
              ) ??
              [],
        );
      }
      if (!mounted) return;
      setState(() => _web = web);
      final code = widget.pairCode;
      // app=1: the TV sends phone browsers that scanned its QR on to this app; not this WebView.
      await web.loadRequest(origin.replace(
        path: '/',
        queryParameters: code == null ? null : {'pair': code, 'app': '1'},
      ));
    } catch (_) {
      _unreachable();
    }
  }

  /// The phone mic becomes the TV's default input as soon as this app is connected.
  Future<void> _autoShareMic(Uri origin) async {
    final mic = _controls.phoneMic;
    if (!mic.supported) return;
    await mic.refresh();
    try {
      final token = await TvVolumeControl.channel.invokeMethod<String>(
        'pairCookie',
        {'origin': origin.origin},
      );
      if (token != null && token.isNotEmpty && mounted && _origin == origin) {
        await mic.autoStart(origin: origin, token: token);
      }
    } catch (_) {}
  }

  Future<void> _message(String raw) async {
    if (!mounted || _origin == null) return;
    try {
      final action = (jsonDecode(raw) as Map<String, dynamic>)['action'];
      final control = _controls.hardwareVolume;
      switch (action) {
        case 'status':
          await control.refresh();
        case 'start':
          if (!control.supported) return;
          final token = await TvVolumeControl.channel.invokeMethod<String>(
            'pairCookie',
            {'origin': _origin!.origin},
          );
          if (token == null || token.isEmpty) {
            control.error = 'Pair with the TV using its six-digit code first.';
          } else {
            await control.start(origin: _origin!, token: token);
          }
        case 'stop':
          await control.stop();
        case 'mic_start':
          final mic = _controls.phoneMic;
          final cookie = await TvVolumeControl.channel.invokeMethod<String>(
            'pairCookie',
            {'origin': _origin!.origin},
          );
          await mic.start(origin: _origin!, token: cookie ?? '');
        case 'mic_stop':
          await _controls.phoneMic.stop();
        case 'speaker_start':
          final cookie = await TvVolumeControl.channel.invokeMethod<String>(
            'pairCookie',
            {'origin': _origin!.origin},
          );
          await _controls.phoneSpeaker.start(
            origin: _origin!,
            token: cookie ?? '',
          );
        case 'speaker_stop':
          await _controls.phoneSpeaker.stop();
        case 'exit':
          widget.onExit();
          return;
        case 'address':
          widget.onSwitch();
          return;
        default:
          return;
      }
      _syncNativeState();
    } catch (_) {
      _controls.hardwareVolume.error =
          'Could not change TV volume controls. Try again.';
      _syncNativeState();
    }
  }

  void _syncNativeState() {
    if (!_ready || _web == null || !mounted) return;
    final control = _controls.hardwareVolume;
    final data = jsonEncode({
      'supported': control.supported,
      'enabled': control.enabled,
      'busy': control.busy,
      'error': control.error,
      'mic': {
        'supported': _controls.phoneMic.supported,
        'enabled': _controls.phoneMic.enabled,
        'busy': _controls.phoneMic.busy,
        'error': _controls.phoneMic.error,
      },
      'speaker': {
        'supported': _controls.phoneSpeaker.supported,
        'enabled': _controls.phoneSpeaker.enabled,
        'busy': _controls.phoneSpeaker.busy,
        'error': _controls.phoneSpeaker.error,
      },
    });
    unawaited(
      _web!
          .runJavaScript('window.NexusNative?.update($data);')
          .catchError((_) {}),
    );
  }

  @override
  void dispose() {
    _controls.hardwareVolume.removeListener(_syncNativeState);
    _controls.phoneMic.removeListener(_syncNativeState);
    _controls.phoneSpeaker.removeListener(_syncNativeState);
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final web = _web;
    return PopScope(
      canPop: false,
      onPopInvokedWithResult: (didPop, _) {
        if (!didPop) widget.onExit();
      },
      child: Scaffold(
        backgroundColor: const Color(0xFF0B111D),
        body: SafeArea(
          child: Stack(
            children: [
              if (web != null) WebViewWidget(controller: web),
              if (!_ready)
                Container(
                  color: const Color(0xFF0B111D),
                  alignment: Alignment.center,
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      const CircularProgressIndicator(),
                      const SizedBox(height: 20),
                      Text('Connecting to ${widget.name}…',
                          style: const TextStyle(color: Color(0xFF9AA7B8), fontSize: 15)),
                      const SizedBox(height: 28),
                      TextButton(onPressed: widget.onSwitch, child: const Text('Connect to another TV')),
                    ],
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }
}
