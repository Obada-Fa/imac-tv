import 'dart:async';
import 'dart:io';
import 'dart:math' as math;
import 'package:flutter/foundation.dart';
import 'package:sensors_plus/sensors_plus.dart';

/// Air pointer: hold the button and tilt the phone to aim the TV's mouse pointer.
///
/// All the work happens here on the phone: reading the gyro, working out which way is up,
/// ignoring hand tremor, smoothing, the speed curve and rounding to whole pixels. The TV
/// only gets small binary records ([type, dx, dy]) over one open connection and hands
/// each to XTest, so it pays nothing for the maths.

/// Record types on the wire: 1 byte type, then dx and dy as little-endian int16.
class AirRecord {
  static const move = 0, begin = 1, end = 2, click = 3, rightClick = 4;
  static const size = 5;

  static Uint8List encode(int type, [int dx = 0, int dy = 0]) {
    final data = ByteData(size)
      ..setUint8(0, type)
      ..setInt16(1, dx.clamp(-300, 300), Endian.little)
      ..setInt16(3, dy.clamp(-300, 300), Endian.little);
    return data.buffer.asUint8List();
  }
}

/// Turns gyro rates into whole-pixel pointer steps. Pure: no sensors, no clock.
class AirMath {
  AirMath({
    this.deadZone = 0.035,
    this.slowGain = 1100,
    this.fastGain = 3300,
    this.fastFrom = 0.4,
    this.fastAt = 2.6,
  });

  /// rad/s under which a hand is only trembling.
  final double deadZone;

  /// Pixels per radian when turning slowly (precise) and quickly (crosses the screen).
  final double slowGain, fastGain;

  /// Turning speed (rad/s) where the gain starts rising and where it tops out.
  final double fastFrom, fastAt;

  // Which way is up, in the phone's axes, from the accelerometer (low-passed).
  double _ux = 0, _uy = 0, _uz = 1;
  bool _hasUp = false;
  double _sx = 0, _sy = 0; // smoothed rates
  double _remX = 0, _remY = 0; // sub-pixel remainders

  void reset() {
    _sx = _sy = _remX = _remY = 0;
  }

  /// Accelerometer sample (m/s², gravity included).
  void gravity(double ax, double ay, double az) {
    final n = math.sqrt(ax * ax + ay * ay + az * az);
    if (n < 4) return; // free fall or a jolt: keep the old idea of up
    final x = ax / n, y = ay / n, z = az / n;
    const k = 0.15;
    if (!_hasUp) {
      _ux = x;
      _uy = y;
      _uz = z;
      _hasUp = true;
    } else {
      _ux += (x - _ux) * k;
      _uy += (y - _uy) * k;
      _uz += (z - _uz) * k;
    }
  }

  /// A gyro sample (rad/s around the phone's x, y, z) covering [dt] seconds.
  /// Returns whole pixels to move now; the rest is kept for the next sample.
  (int, int) step(double gx, double gy, double gz, double dt) {
    if (dt <= 0 || dt > 0.1) dt = 0.01;
    // Up in the phone's frame.
    var n = math.sqrt(_ux * _ux + _uy * _uy + _uz * _uz);
    final ux = _ux / n, uy = _uy / n, uz = _uz / n;
    // Turning about "up" moves the pointer sideways, whether the phone lies flat or stands up.
    final yaw = gx * ux + gy * uy + gz * uz;
    // Tilting about the phone's level side-to-side axis moves it up and down.
    var hx = 1 - ux * ux, hy = -ux * uy, hz = -ux * uz; // phone x with its vertical part removed
    n = math.sqrt(hx * hx + hy * hy + hz * hz);
    if (n < 0.3) {
      // Phone on its side (x points up): use its y axis instead.
      hx = -uy * ux;
      hy = 1 - uy * uy;
      hz = -uy * uz;
      n = math.sqrt(hx * hx + hy * hy + hz * hz);
    }
    final pitch = (gx * hx + gy * hy + gz * hz) / n;
    var rx = -yaw, ry = -pitch;

    // Tremor: a soft dead zone, so slow aiming doesn't jump when it starts.
    final speed = math.sqrt(rx * rx + ry * ry);
    if (speed <= deadZone) {
      rx = ry = 0;
    } else {
      final keep = (speed - deadZone) / speed;
      rx *= keep;
      ry *= keep;
    }
    // Smoothing that gets out of the way when moving fast (a simple one-euro style filter).
    final alpha = 0.3 + 0.6 * ((speed - deadZone) / 1.5).clamp(0.0, 1.0);
    _sx += (rx - _sx) * alpha;
    _sy += (ry - _sy) * alpha;

    final s = math.sqrt(_sx * _sx + _sy * _sy);
    final t = ((s - fastFrom) / (fastAt - fastFrom)).clamp(0.0, 1.0);
    final gain = slowGain + (fastGain - slowGain) * t * t;
    _remX += _sx * dt * gain;
    _remY += _sy * dt * gain;
    final ix = _remX.truncate(), iy = _remY.truncate();
    _remX -= ix;
    _remY -= iy;
    return (ix, iy);
  }
}

/// Where records go: [AirLink] on a phone, a fake in tests.
abstract class AirSink {
  bool get closed;
  bool get refused;
  void send(List<int> records);
  Future<void> close();
}

/// One long POST to the TV's /api/air carrying chunked [AirRecord]s.
class AirLink implements AirSink {
  AirLink(this._socket) {
    _socket.listen((data) {
      // The TV answers only when it refuses or when the stream ends.
      final head = String.fromCharCodes(data.take(16));
      if (head.contains(' 401')) refused = true;
    }, onError: (_) => closed = true, onDone: () => closed = true, cancelOnError: true);
  }

  final Socket _socket;
  @override
  bool closed = false;
  @override
  bool refused = false;

  static Future<AirLink> open(Uri origin, String cookie) async {
    final port = origin.hasPort ? origin.port : 80;
    final socket = await Socket.connect(origin.host, port, timeout: const Duration(seconds: 3));
    socket.setOption(SocketOption.tcpNoDelay, true);
    socket.add('POST /api/air HTTP/1.1\r\n'
            'Host: ${origin.host}:$port\r\n'
            'Cookie: $cookie\r\n'
            'Content-Type: application/octet-stream\r\n'
            'Transfer-Encoding: chunked\r\n\r\n'
        .codeUnits);
    return AirLink(socket);
  }

  @override
  void send(List<int> records) {
    if (closed || records.isEmpty) return;
    try {
      _socket.add([...'${records.length.toRadixString(16)}\r\n'.codeUnits, ...records, 13, 10]);
    } catch (_) {
      closed = true;
    }
  }

  @override
  Future<void> close() async {
    if (closed) return;
    closed = true;
    try {
      _socket.add('0\r\n\r\n'.codeUnits);
      await _socket.flush();
    } catch (_) {}
    _socket.destroy();
  }
}

typedef AirLinkOpener = Future<AirSink> Function(Uri origin, String cookie);

class AirPointer extends ChangeNotifier {
  AirPointer({
    AirLinkOpener? opener,
    Stream<GyroscopeEvent> Function()? gyro,
    Stream<AccelerometerEvent> Function()? accel,
  })  : _opener = opener ?? AirLink.open,
        _gyro = gyro ?? (() => gyroscopeEventStream(samplingPeriod: const Duration(milliseconds: 10))),
        _accel = accel ?? (() => accelerometerEventStream(samplingPeriod: const Duration(milliseconds: 20)));

  final AirLinkOpener _opener;
  final Stream<GyroscopeEvent> Function() _gyro;
  final Stream<AccelerometerEvent> Function() _accel;

  bool get supported => !kIsWeb && defaultTargetPlatform == TargetPlatform.android;

  /// A press shorter than this is a click: its first moments don't move the pointer.
  static const tapMs = 220;

  /// At most this often a move goes out (≈60 Hz); samples in between add up.
  static const sendEveryUs = 16000;

  /// The connection closes after this long without aiming.
  static const idle = Duration(seconds: 30);

  final aim = AirMath();
  bool aiming = false;
  String? error;
  AirSink? _link;
  Uri? _origin;
  String? _cookie;
  StreamSubscription<GyroscopeEvent>? _gyroSub;
  StreamSubscription<AccelerometerEvent>? _accelSub;
  DateTime? _last, _downAt;
  int _lastSendUs = 0, _pendX = 0, _pendY = 0;
  Timer? _idleTimer;
  int _press = 0;

  Future<AirSink?> _ensureLink(Uri origin, String cookie) async {
    final link = _link;
    if (link != null && !link.closed && _origin == origin && _cookie == cookie) return link;
    await link?.close();
    _link = null;
    try {
      _link = await _opener(origin, cookie);
      _origin = origin;
      _cookie = cookie;
      error = null;
    } catch (_) {
      error = 'Could not reach the TV for the air pointer.';
    }
    return _link;
  }

  /// The button went down: start aiming.
  Future<void> down({required Uri origin, required String cookie}) async {
    if (!supported) return;
    if (cookie.endsWith('=')) {
      error = 'Pair with the TV using its six-digit code first.';
      notifyListeners();
      return;
    }
    final press = ++_press;
    _idleTimer?.cancel();
    _downAt = DateTime.now();
    aiming = true;
    final link = await _ensureLink(origin, cookie);
    if (press != _press || !aiming) return; // let go while connecting
    if (link == null) {
      aiming = false;
      notifyListeners();
      return;
    }
    if (link.refused) {
      error = 'Pair with the TV using its six-digit code first.';
      aiming = false;
      notifyListeners();
      return;
    }
    aim.reset();
    _last = null;
    _pendX = _pendY = 0;
    link.send(AirRecord.encode(AirRecord.begin));
    _accelSub = _accel().listen((e) => aim.gravity(e.x, e.y, e.z), onError: (_) {});
    _gyroSub = _gyro().listen(_sample, onError: (_) {
      error = 'This phone has no gyroscope.';
      notifyListeners();
    });
    notifyListeners();
  }

  void _sample(GyroscopeEvent e) {
    final now = e.timestamp;
    final dt = _last == null ? 0.01 : now.difference(_last!).inMicroseconds / 1e6;
    _last = now;
    final (dx, dy) = aim.step(e.x, e.y, e.z, dt);
    // The first moments of a press may be a tap: don't let the thumb's push move the pointer.
    if (_downAt != null && now.difference(_downAt!).inMilliseconds < tapMs ~/ 2) return;
    _pendX += dx;
    _pendY += dy;
    final us = now.microsecondsSinceEpoch;
    if ((_pendX != 0 || _pendY != 0) && us - _lastSendUs >= sendEveryUs) {
      _lastSendUs = us;
      _flush();
    }
  }

  void _flush() {
    final link = _link;
    if (link == null) return;
    final out = <int>[];
    while (_pendX != 0 || _pendY != 0) {
      final x = _pendX.clamp(-300, 300), y = _pendY.clamp(-300, 300);
      out.addAll(AirRecord.encode(AirRecord.move, x, y));
      _pendX -= x;
      _pendY -= y;
    }
    link.send(out);
  }

  /// The button came up: stop aiming; a quick press clicks.
  Future<void> up() async {
    if (!aiming) return;
    aiming = false;
    final quick = _downAt != null && DateTime.now().difference(_downAt!).inMilliseconds < tapMs;
    await _gyroSub?.cancel();
    await _accelSub?.cancel();
    _gyroSub = _accelSub = null;
    final link = _link;
    if (link != null) {
      if (!quick) _flush();
      _pendX = _pendY = 0;
      link.send([
        ...AirRecord.encode(AirRecord.end),
        if (quick) ...AirRecord.encode(AirRecord.click),
      ]);
    }
    _idleTimer?.cancel();
    _idleTimer = Timer(idle, stop);
    notifyListeners();
  }

  Future<void> stop() async {
    _press++;
    aiming = false;
    _idleTimer?.cancel();
    await _gyroSub?.cancel();
    await _accelSub?.cancel();
    _gyroSub = _accelSub = null;
    await _link?.close();
    _link = null;
    notifyListeners();
  }

  @override
  void dispose() {
    unawaited(stop());
    super.dispose();
  }
}
