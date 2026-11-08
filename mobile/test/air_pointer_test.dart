import 'dart:async';
import 'dart:typed_data';
import 'package:flutter_test/flutter_test.dart';
import 'package:nexus_tv/air_pointer.dart';
import 'package:sensors_plus/sensors_plus.dart';

(int, int) run(AirMath m, double gx, double gy, double gz, {int samples = 100}) {
  var x = 0, y = 0;
  for (var i = 0; i < samples; i++) {
    final (dx, dy) = m.step(gx, gy, gz, 0.01);
    x += dx;
    y += dy;
  }
  return (x, y);
}

void main() {
  test('records are 5 bytes: type then little-endian dx, dy, clamped', () {
    final r = AirRecord.encode(AirRecord.move, 500, -2);
    expect(r.length, 5);
    final d = ByteData.sublistView(r);
    expect(d.getUint8(0), 0);
    expect(d.getInt16(1, Endian.little), 300);
    expect(d.getInt16(3, Endian.little), -2);
  });

  group('phone lying flat, screen up, top towards the TV', () {
    AirMath flat() => AirMath()..gravity(0, 0, 9.8);

    test('turning left moves the pointer left', () {
      final (x, y) = run(flat(), 0, 0, 0.5);
      expect(x, lessThan(-100));
      expect(y.abs(), lessThan(3));
    });
    test('tilting the top up moves the pointer up', () {
      final (x, y) = run(flat(), 0.5, 0, 0);
      expect(y, lessThan(-100));
      expect(x.abs(), lessThan(3));
    });
  });

  test('phone held upright gives the same directions', () {
    final m = AirMath()..gravity(0, 9.8, 0);
    final (x, _) = run(m, 0, 0.5, 0); // turning left about the world's up = the phone's y
    expect(x, lessThan(-100));
    final (_, y) = run(AirMath()..gravity(0, 9.8, 0), 0.5, 0, 0);
    expect(y, lessThan(-100));
  });

  test('hand tremor does not move the pointer', () {
    final (x, y) = run(AirMath()..gravity(0, 0, 9.8), 0.02, -0.015, 0.025, samples: 300);
    expect(x, 0);
    expect(y, 0);
  });

  test('fast turns travel further per radian than slow ones', () {
    // The same angle (0.5 rad) turned slowly and quickly.
    final (slow, _) = run(AirMath()..gravity(0, 0, 9.8), 0, 0, 0.25, samples: 200);
    final (fast, _) = run(AirMath()..gravity(0, 0, 9.8), 0, 0, 2.5, samples: 20);
    expect(fast.abs(), greaterThan(slow.abs() * 1.5));
  });

  group('AirPointer', () {
    late FakeSink sink;
    late StreamController<GyroscopeEvent> gyro;
    late StreamController<AccelerometerEvent> accel;
    late AirPointer air;
    final tv = Uri.parse('http://192.168.1.5:8765');

    setUp(() {
      sink = FakeSink();
      gyro = StreamController<GyroscopeEvent>.broadcast();
      accel = StreamController<AccelerometerEvent>.broadcast();
      air = AirPointer(opener: (_, _) async => sink, gyro: () => gyro.stream, accel: () => accel.stream);
    });
    tearDown(() async {
      await air.stop();
      await gyro.close();
      await accel.close();
    });

    List<int> kinds() => [for (var i = 0; i < sink.bytes.length; i += 5) sink.bytes[i]];

    test('a quick press clicks without moving the pointer', () async {
      await air.down(origin: tv, cookie: 'imactv=x');
      gyro.add(GyroscopeEvent(0, 0, 1.0, DateTime.now()));
      await Future<void>.delayed(Duration.zero);
      await air.up();
      expect(kinds(), [AirRecord.begin, AirRecord.end, AirRecord.click]);
    });

    test('a hold sends moves at most every 16 ms, then ends without a click', () async {
      await air.down(origin: tv, cookie: 'imactv=x');
      accel.add(AccelerometerEvent(0, 0, 9.8, DateTime.now()));
      await Future<void>.delayed(const Duration(milliseconds: AirPointer.tapMs + 10));
      final t0 = DateTime.now();
      for (var i = 0; i < 50; i++) {
        gyro.add(GyroscopeEvent(0, 0, 1.0, t0.add(Duration(milliseconds: 5 * i))));
      }
      await Future<void>.delayed(Duration.zero);
      await air.up();
      final k = kinds();
      expect(k.first, AirRecord.begin);
      expect(k.last, AirRecord.end);
      final moves = k.where((t) => t == AirRecord.move).length;
      expect(moves, inInclusiveRange(10, 17)); // 250 ms of samples at 200 Hz, sent at ≤60 Hz
      expect(k, isNot(contains(AirRecord.click)));
      // Turning left: every move goes left.
      for (var i = 0; i < sink.bytes.length; i += 5) {
        if (sink.bytes[i] == AirRecord.move) {
          expect(ByteData.sublistView(Uint8List.fromList(sink.bytes), i + 1, i + 3).getInt16(0, Endian.little),
              lessThan(0));
        }
      }
    });

    test('a refused phone stops aiming and says to pair', () async {
      sink.refused = true;
      await air.down(origin: tv, cookie: 'imactv=');
      expect(air.aiming, isFalse);
      expect(air.error, contains('Pair'));
    });
  });
}

class FakeSink implements AirSink {
  final bytes = <int>[];
  @override
  bool closed = false;
  @override
  bool refused = false;
  @override
  void send(List<int> records) => bytes.addAll(records);
  @override
  Future<void> close() async => closed = true;
}
