import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'connect_page.dart';
import 'discovery.dart';
import 'native.dart';
import 'tv_store.dart';
import 'tv_web_page.dart';

/// Nexus TV: the phone remote for a Nexus TV. The remote itself is the TV's own
/// phone page; this app adds TV choice, QR links and Android's native extras.
void main() => runApp(const NexusTvApp());

class NexusTvApp extends StatefulWidget {
  const NexusTvApp({super.key, this.store, this.controls, this.links, this.discover = discoverTvs});
  final TvStore? store;
  final NativeControls? controls;
  final TvLinks? links;
  final Stream<TvInfo> Function() discover;
  @override
  State<NexusTvApp> createState() => _NexusTvAppState();
}

/// What the app shows: the remote for one TV, or the connect page.
sealed class _View {}

class _Remote extends _View {
  _Remote(this.origin, this.code);
  final Uri origin;
  final String? code;
}

class _Connect extends _View {
  _Connect({this.failed, this.back});
  final SavedTv? failed;

  /// The remote to return to when the user only looked at other TVs.
  final _Remote? back;
}

class _NexusTvAppState extends State<NexusTvApp> {
  late final TvStore store = widget.store ?? TvStore();
  late final NativeControls controls = widget.controls ?? NativeControls();
  late final TvLinks links = widget.links ?? TvLinks();
  final _messenger = GlobalKey<ScaffoldMessengerState>();
  final _navigator = GlobalKey<NavigatorState>();
  StreamSubscription<String>? _linkSub;
  _View? _view;
  Uri? _controlsFor;

  @override
  void initState() {
    super.initState();
    unawaited(_start());
  }

  Future<void> _start() async {
    await Future.wait([store.load(), controls.initialize().catchError((_) {})]);
    _linkSub = links.links.listen(_handleLink);
    final initial = await links.initial();
    if (!mounted) return;
    if (initial != null && _handleLink(initial)) return;
    final last = store.last;
    setState(() => _view = last == null ? _Connect() : _Remote(last.origin, null));
  }

  /// A QR scan or nexustv:// link. False when it isn't a TV on this network.
  bool _handleLink(String raw) {
    final link = parseLink(raw);
    if (link == null || !isLocalHost(link.origin.host)) {
      _say("That isn't a Nexus TV QR code.");
      return false;
    }
    _open(link.origin, code: link.code);
    return true;
  }

  Future<void> _open(Uri origin, {String? code}) async {
    // Volume buttons, mic and speaker belong to one TV: stop them before another.
    if (_controlsFor != null && _controlsFor != origin) await controls.stopAll();
    _controlsFor = origin;
    if (mounted) setState(() => _view = _Remote(origin, code));
  }

  Future<void> _scanQr() async {
    final navigator = _navigator.currentState;
    if (navigator == null) return;
    final text = await links.scanQr(navigator);
    if (text != null) _handleLink(text);
  }

  void _say(String text) =>
      _messenger.currentState?..hideCurrentSnackBar()..showSnackBar(SnackBar(content: Text(text)));

  @override
  void dispose() {
    _linkSub?.cancel();
    super.dispose();
  }

  Widget _page() {
    final view = _view;
    switch (view) {
      case null:
        return const Scaffold(backgroundColor: Color(0xFF0B111D), body: Center(child: CircularProgressIndicator()));
      case _Remote(:final origin, :final code):
        return TvWebPage(
          key: ValueKey('$origin|$code'),
          controls: controls,
          origin: origin,
          pairCode: code,
          name: store.find(origin)?.name ?? 'your TV',
          onConnected: (tv) {
            final saved = store.find(origin);
            // An older TV has no name of its own; keep one the phone already knows.
            unawaited(store.remember(origin, tv.name == 'Nexus TV' && saved != null ? saved.name : tv.name));
          },
          onUnreachable: () => setState(() => _view = _Connect(
                failed: store.find(origin) ?? SavedTv(origin: origin, name: displayAddress(origin), lastUsed: 0),
              )),
          onSwitch: () => setState(() => _view = _Connect(back: view)),
          onExit: SystemNavigator.pop,
        );
      case _Connect(:final failed, :final back):
        return ConnectPage(
          key: ValueKey(failed?.origin ?? back),
          store: store,
          failed: failed,
          discover: widget.discover,
          onOpen: (origin) => _open(origin),
          onScanQr: _scanQr,
          onCancel: back == null ? null : () => setState(() => _view = back),
        );
    }
  }

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Nexus TV',
      scaffoldMessengerKey: _messenger,
      navigatorKey: _navigator,
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        useMaterial3: true,
        brightness: Brightness.dark,
        scaffoldBackgroundColor: const Color(0xFF0B111D),
        colorScheme: ColorScheme.fromSeed(seedColor: const Color(0xFF8AE3CB), brightness: Brightness.dark),
      ),
      home: _page(),
    );
  }
}
