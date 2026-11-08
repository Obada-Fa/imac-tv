import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'package:flutter/foundation.dart';
import 'package:sqflite/sqflite.dart';

/// The TV port every Nexus TV serves its phone page on.
const tvPort = 8765;

/// A TV this phone has opened before. Its pairing cookie lives in the WebView's
/// cookie store per origin, so switching back to a TV needs no new code.
class SavedTv {
  const SavedTv({required this.origin, required this.name, required this.lastUsed});
  final Uri origin;
  final String name;
  final int lastUsed;

  /// "192.168.1.20" for the default port, "192.168.1.20:9000" otherwise.
  String get address => displayAddress(origin);
}

/// A TV that answered on the network.
class TvInfo {
  const TvInfo({required this.origin, required this.name});
  final Uri origin;
  final String name;
  String get address => displayAddress(origin);
}

/// Where a QR code or nexustv:// link points: a TV and, maybe, its pairing code.
class TvLink {
  const TvLink(this.origin, [this.code]);
  final Uri origin;
  final String? code;
}

String displayAddress(Uri origin) =>
    origin.port == tvPort && origin.scheme == 'http' ? origin.host : '${origin.host}:${origin.port}';

/// "192.168.1.20", "192.168.1.20:8765" or "http://192.168.1.20:8765/" -> http://192.168.1.20:8765
Uri parseOrigin(String address) {
  final input = address.trim();
  final uri = Uri.tryParse(input.contains('://') ? input : 'http://$input');
  if (uri == null || !{'http', 'https'}.contains(uri.scheme) || uri.host.isEmpty ||
      uri.userInfo.isNotEmpty || uri.hasQuery || uri.hasFragment ||
      (uri.path.isNotEmpty && uri.path != '/')) {
    throw const FormatException('Enter the TV address, for example 192.168.1.20');
  }
  return Uri(scheme: uri.scheme, host: uri.host,
      port: uri.hasPort ? uri.port : (uri.scheme == 'https' ? 443 : tvPort));
}

/// The TV's QR (`http://IP:8765/?pair=CODE`) or its app link
/// (`nexustv://pair?host=IP:PORT&code=CODE`). Anything else is null.
TvLink? parseLink(String raw) {
  final uri = Uri.tryParse(raw.trim());
  if (uri == null) return null;
  try {
    if (uri.scheme == 'nexustv') {
      if (uri.host != 'pair') return null;
      final host = uri.queryParameters['host'];
      if (host == null || host.isEmpty) return null;
      return TvLink(parseOrigin(host), _code(uri.queryParameters['code']));
    }
    if (uri.scheme == 'http' || uri.scheme == 'https') {
      if (uri.path.isNotEmpty && uri.path != '/') return null;
      final origin = parseOrigin(Uri(scheme: uri.scheme, host: uri.host,
          port: uri.hasPort ? uri.port : null).toString());
      final q = uri.queryParameters;
      return TvLink(origin, _code(q['pair'] ?? q['code']));
    }
  } on FormatException {
    return null;
  }
  return null;
}

String? _code(String? code) => code != null && RegExp(r'^\d{6}$').hasMatch(code) ? code : null;

/// Only TVs on this network may be opened from a link: a web page can open a
/// nexustv:// link too, and the app's WebView can share the phone's mic.
bool isLocalHost(String host) {
  final h = host.toLowerCase();
  if (h == 'localhost' || h.endsWith('.local') || h.endsWith('.lan') ||
      h.endsWith('.home') || h.endsWith('.home.arpa')) {
    return true;
  }
  final parts = h.split('.');
  if (parts.length == 1) return RegExp(r'^[a-z0-9-]+$').hasMatch(h);
  if (parts.length != 4) return false;
  final n = parts.map(int.tryParse).toList();
  if (n.any((x) => x == null || x < 0 || x > 255)) return false;
  final a = n[0]!, b = n[1]!;
  return a == 10 || a == 127 || (a == 172 && b >= 16 && b <= 31) || (a == 192 && b == 168) ||
      (a == 169 && b == 254) || (a == 100 && b >= 64 && b <= 127);
}

/// Ask a TV who it is. Null when nothing answers like a Nexus TV in time.
Future<TvInfo?> probeTv(Uri origin, {Duration timeout = const Duration(seconds: 3)}) async {
  final client = HttpClient()..connectionTimeout = timeout;
  try {
    final request = await client.getUrl(origin.replace(path: '/api/hello')).timeout(timeout);
    request.followRedirects = false;
    final response = await request.close().timeout(timeout);
    final raw = await utf8.decoder.bind(response).join().timeout(timeout);
    final body = jsonDecode(raw);
    if (body is! Map) return null;
    if (response.statusCode == 200 && body['app'] == 'nexus-tv') {
      final name = body['name']?.toString().trim() ?? '';
      return TvInfo(origin: origin, name: name.isEmpty ? 'Nexus TV' : name);
    }
    // A TV from before /api/hello answers its own JSON 404.
    if (response.statusCode == 404 && body['error'] == 'Not found') {
      return TvInfo(origin: origin, name: 'Nexus TV');
    }
    return null;
  } catch (_) {
    return null;
  } finally {
    client.close(force: true);
  }
}

/// The TVs this phone knows, most recently used first.
class TvStore extends ChangeNotifier {
  TvStore({this.databasePath});

  /// Tests pass inMemoryDatabasePath.
  final String? databasePath;
  Database? _db;
  List<SavedTv> _tvs = [];

  List<SavedTv> get tvs => List.unmodifiable(_tvs);
  SavedTv? get last => _tvs.isEmpty ? null : _tvs.first;

  SavedTv? find(Uri origin) {
    for (final tv in _tvs) {
      if (tv.origin == origin) return tv;
    }
    return null;
  }

  Future<void> load() async {
    try {
      _db = await openDatabase(databasePath ?? '${await getDatabasesPath()}/nexus_remote.sqlite');
      await _db!.execute('CREATE TABLE IF NOT EXISTS tvs '
          '(origin TEXT PRIMARY KEY, name TEXT NOT NULL, last_used INTEGER NOT NULL)');
      await _migrate();
      final rows = await _db!.query('tvs', orderBy: 'last_used DESC');
      _tvs = [
        for (final row in rows)
          if (Uri.tryParse(row['origin'] as String) case final origin?)
            SavedTv(origin: origin, name: row['name'] as String, lastUsed: row['last_used'] as int),
      ];
    } catch (_) {
      _tvs = [];
    }
    notifyListeners();
  }

  /// The earlier app kept one TV address in a settings table.
  Future<void> _migrate() async {
    final db = _db!;
    final tables = await db.query('sqlite_master', where: 'type = ? AND name = ?', whereArgs: ['table', 'settings']);
    if (tables.isEmpty) return;
    final rows = await db.query('settings', where: 'key = ?', whereArgs: ['imac_address']);
    if (rows.isEmpty) return;
    try {
      final origin = parseOrigin(rows.first['value'] as String);
      await db.insert('tvs', {'origin': origin.toString(), 'name': 'Nexus TV', 'last_used': 1},
          conflictAlgorithm: ConflictAlgorithm.ignore);
    } on FormatException {
      // Nothing usable to carry over.
    }
    await db.delete('settings', where: 'key = ?', whereArgs: ['imac_address']);
  }

  /// Mark a TV as the one in use, with the name it gave.
  Future<void> remember(Uri origin, String name) async {
    final now = DateTime.now().millisecondsSinceEpoch;
    _tvs = [SavedTv(origin: origin, name: name, lastUsed: now), ..._tvs.where((t) => t.origin != origin)];
    notifyListeners();
    try {
      await _db?.insert('tvs', {'origin': origin.toString(), 'name': name, 'last_used': now},
          conflictAlgorithm: ConflictAlgorithm.replace);
    } catch (_) {}
  }

  Future<void> forget(Uri origin) async {
    _tvs = _tvs.where((t) => t.origin != origin).toList();
    notifyListeners();
    try {
      await _db?.delete('tvs', where: 'origin = ?', whereArgs: [origin.toString()]);
    } catch (_) {}
  }
}
