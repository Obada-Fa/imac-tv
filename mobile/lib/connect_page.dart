import 'dart:async';
import 'package:flutter/material.dart';
import 'discovery.dart';
import 'tv_store.dart';

const _bg = Color(0xFF0B111D);
const _accent = Color(0xFF8AE3CB);
const _muted = Color(0xFF9AA7B8);
const _card = Color(0xFF151D2B);

/// Pick a TV: shown when there is none yet, when the last one can't be
/// reached, and from "Switch TV" in the remote.
class ConnectPage extends StatefulWidget {
  const ConnectPage({
    super.key,
    required this.store,
    required this.onOpen,
    required this.onScanQr,
    this.failed,
    this.onCancel,
    this.discover = discoverTvs,
  });

  final TvStore store;
  final void Function(Uri origin) onOpen;
  final Future<void> Function() onScanQr;

  /// The TV that could not be reached, if that is why this page shows.
  final SavedTv? failed;

  /// Back to the TV that is still connected (only from "Switch TV").
  final VoidCallback? onCancel;
  final Stream<TvInfo> Function() discover;

  @override
  State<ConnectPage> createState() => _ConnectPageState();
}

class _ConnectPageState extends State<ConnectPage> {
  final _found = <Uri, TvInfo>{};
  StreamSubscription<TvInfo>? _scan;
  bool _scanning = false;

  @override
  void initState() {
    super.initState();
    widget.store.addListener(_changed);
    _rescan();
  }

  void _changed() => setState(() {});

  void _rescan() {
    _scan?.cancel();
    setState(() {
      _found.clear();
      _scanning = true;
    });
    _scan = widget.discover().listen(
      (tv) => setState(() => _found[tv.origin] = tv),
      onDone: () {
        if (mounted) setState(() => _scanning = false);
      },
    );
  }

  @override
  void dispose() {
    _scan?.cancel();
    widget.store.removeListener(_changed);
    super.dispose();
  }

  Future<void> _enterAddress() async {
    final origin = await showDialog<Uri>(context: context, builder: (_) => const _AddressDialog());
    if (origin != null) widget.onOpen(origin);
  }

  Future<void> _forget(SavedTv tv) async {
    final yes = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text('Forget ${tv.name}?'),
        content: Text('${tv.address} is removed from this phone. You can add it again later.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Cancel')),
          TextButton(onPressed: () => Navigator.pop(context, true), child: const Text('Forget')),
        ],
      ),
    );
    if (yes == true) await widget.store.forget(tv.origin);
  }

  @override
  Widget build(BuildContext context) {
    final failed = widget.failed;
    final saved = widget.store.tvs;
    final savedOrigins = saved.map((t) => t.origin).toSet();
    final newTvs = _found.values.where((t) => !savedOrigins.contains(t.origin)).toList()
      ..sort((a, b) => a.address.compareTo(b.address));

    return PopScope(
      canPop: widget.onCancel == null,
      onPopInvokedWithResult: (didPop, _) {
        if (!didPop) widget.onCancel?.call();
      },
      child: Scaffold(
        backgroundColor: _bg,
        appBar: AppBar(
          backgroundColor: _bg,
          automaticallyImplyLeading: false,
          leading: widget.onCancel == null
              ? null
              : IconButton(icon: const Icon(Icons.close), tooltip: 'Back to the remote', onPressed: widget.onCancel),
          title: Text(widget.onCancel != null ? 'Switch TV' : 'Nexus TV'),
        ),
        body: SafeArea(
          child: ListView(
            padding: const EdgeInsets.fromLTRB(20, 8, 20, 32),
            children: [
              if (failed != null) _FailedCard(tv: failed, onRetry: () => widget.onOpen(failed.origin))
              else if (widget.onCancel == null) ...[
                const Text('Connect to your TV', style: TextStyle(fontSize: 26, fontWeight: FontWeight.w600)),
                const SizedBox(height: 8),
                const Text('Pick your TV below, or scan the QR code on its screen.',
                    style: TextStyle(color: _muted, fontSize: 15)),
              ],
              if (saved.isNotEmpty) ...[
                const _Heading('Your TVs'),
                for (final tv in saved)
                  _TvTile(
                    name: tv.name,
                    address: tv.address,
                    state: _found.containsKey(tv.origin)
                        ? _TvState.online
                        : tv.origin == failed?.origin
                            ? _TvState.unreachable
                            : _TvState.unknown,
                    onTap: () => widget.onOpen(tv.origin),
                    onLongPress: () => _forget(tv),
                  ),
              ],
              _Heading('On this Wi-Fi',
                  trailing: _scanning
                      ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
                      : IconButton(
                          icon: const Icon(Icons.refresh, size: 20),
                          tooltip: 'Look again',
                          visualDensity: VisualDensity.compact,
                          onPressed: _rescan,
                        )),
              for (final tv in newTvs)
                _TvTile(name: tv.name, address: tv.address, state: _TvState.online, onTap: () => widget.onOpen(tv.origin)),
              if (newTvs.isEmpty)
                Padding(
                  padding: const EdgeInsets.symmetric(vertical: 10),
                  child: Text(
                    _scanning
                        ? 'Looking for TVs…'
                        : _found.isEmpty
                            ? 'No TVs found. Check that the TV is on and this phone uses the same Wi-Fi.'
                            : 'No other TVs found.',
                    style: const TextStyle(color: _muted),
                  ),
                ),
              const SizedBox(height: 24),
              FilledButton.icon(
                onPressed: widget.onScanQr,
                icon: const Icon(Icons.qr_code_scanner),
                label: const Text('Scan the QR code on the TV'),
                style: FilledButton.styleFrom(minimumSize: const Size.fromHeight(52)),
              ),
              const SizedBox(height: 8),
              TextButton(onPressed: _enterAddress, child: const Text('Enter address')),
            ],
          ),
        ),
      ),
    );
  }
}

class _AddressDialog extends StatefulWidget {
  const _AddressDialog();
  @override
  State<_AddressDialog> createState() => _AddressDialogState();
}

class _AddressDialogState extends State<_AddressDialog> {
  final _controller = TextEditingController();
  String? _error;

  void _submit() {
    try {
      Navigator.pop(context, parseOrigin(_controller.text));
    } on FormatException catch (e) {
      setState(() => _error = e.message);
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('TV address'),
      content: TextField(
        controller: _controller,
        autofocus: true,
        keyboardType: TextInputType.url,
        autocorrect: false,
        decoration: InputDecoration(hintText: '192.168.1.20', errorText: _error),
        onSubmitted: (_) => _submit(),
      ),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context), child: const Text('Cancel')),
        FilledButton(onPressed: _submit, child: const Text('Connect')),
      ],
    );
  }
}

class _FailedCard extends StatelessWidget {
  const _FailedCard({required this.tv, required this.onRetry});
  final SavedTv tv;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(20),
      decoration: BoxDecoration(color: _card, borderRadius: BorderRadius.circular(20)),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Icon(Icons.wifi_off_rounded, color: Color(0xFFFFB37A), size: 32),
          const SizedBox(height: 12),
          Text("Can't reach ${tv.name}", style: const TextStyle(fontSize: 22, fontWeight: FontWeight.w600)),
          const SizedBox(height: 6),
          Text('${tv.address} did not answer. Make sure the TV is on and this phone is on the same Wi-Fi, '
              'or connect to another TV below.',
              style: const TextStyle(color: _muted, fontSize: 15)),
          const SizedBox(height: 16),
          FilledButton.tonalIcon(onPressed: onRetry, icon: const Icon(Icons.refresh), label: const Text('Try again')),
        ],
      ),
    );
  }
}

class _Heading extends StatelessWidget {
  const _Heading(this.text, {this.trailing});
  final String text;
  final Widget? trailing;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(top: 24, bottom: 6),
      child: Row(children: [
        Expanded(
          child: Text(text.toUpperCase(),
              style: const TextStyle(color: _muted, fontSize: 12, letterSpacing: 1.2, fontWeight: FontWeight.w600)),
        ),
        ?trailing,
      ]),
    );
  }
}

enum _TvState { online, unreachable, unknown }

class _TvTile extends StatelessWidget {
  const _TvTile({required this.name, required this.address, required this.state, required this.onTap, this.onLongPress});
  final String name;
  final String address;
  final _TvState state;
  final VoidCallback onTap;
  final VoidCallback? onLongPress;

  @override
  Widget build(BuildContext context) {
    final (label, color) = switch (state) {
      _TvState.online => ('Online', _accent),
      _TvState.unreachable => ('Not reachable', const Color(0xFFFFB37A)),
      _TvState.unknown => ('', _muted),
    };
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Material(
        color: _card,
        borderRadius: BorderRadius.circular(16),
        child: InkWell(
          borderRadius: BorderRadius.circular(16),
          onTap: onTap,
          onLongPress: onLongPress,
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
            child: Row(children: [
              const Icon(Icons.tv_rounded, color: _accent),
              const SizedBox(width: 14),
              Expanded(
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text(name, style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w500)),
                  const SizedBox(height: 2),
                  Text(address, style: const TextStyle(color: _muted, fontSize: 13)),
                ]),
              ),
              if (label.isNotEmpty) ...[
                Container(width: 8, height: 8, decoration: BoxDecoration(color: color, shape: BoxShape.circle)),
                const SizedBox(width: 6),
                Text(label, style: TextStyle(color: color, fontSize: 13)),
              ],
            ]),
          ),
        ),
      ),
    );
  }
}
