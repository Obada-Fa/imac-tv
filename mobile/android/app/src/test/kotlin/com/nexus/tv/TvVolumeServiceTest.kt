package com.nexus.tv

import android.content.Intent
import android.media.AudioManager
import android.media.VolumeProvider
import android.media.session.MediaSession
import android.os.Looper
import android.view.KeyEvent
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.android.controller.ServiceController
import org.robolectric.annotation.Config
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [36])
class TvVolumeServiceTest {
    private lateinit var server: MockWebServer
    private lateinit var controller: ServiceController<TvVolumeService>
    private val commands = LinkedBlockingQueue<RecordedRequest>()
    @Volatile private var revoked = false
    @Volatile private var tvState = """{"volume":40,"playback":{"active":true,"paused":false,"title":"Test film","duration":200,"position":30}}"""

    @Before fun setup() {
        server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                if (revoked) return MockResponse().setResponseCode(401)
                if (request.path == "/api/commands") {
                    commands.add(request)
                    return MockResponse().setBody("""{"ok":true,"result":{"volume":45}}""")
                }
                return MockResponse().setBody(tvState)
            }
        }
        server.start()
        controller = Robolectric.buildService(TvVolumeService::class.java).create()
    }

    @After fun cleanup() {
        TvVolumeService.stop(controller.get())
        controller.destroy()
        server.shutdown()
    }

    private fun start() {
        controller.get().onStartCommand(Intent(controller.get(), TvVolumeService::class.java)
            .putExtra("origin", server.url("/").toString())
            .putExtra("cookie", "imactv=paired-token"), 0, 1)
        assertEquals(true, TvVolumeService.status()["enabled"])
    }

    private fun field(name: String): Any {
        val field = TvVolumeService::class.java.getDeclaredField(name)
        field.isAccessible = true
        return field.get(controller.get())
    }

    @Test fun remoteVolumeCallbackWorksWithoutFlutterActivityAndStopReleasesSession() {
        start()
        val provider = field("provider") as VolumeProvider
        val session = field("session") as MediaSession
        assertTrue(session.isActive)
        provider.onAdjustVolume(AudioManager.ADJUST_RAISE)
        val up = commands.poll(3, TimeUnit.SECONDS)!!
        assertEquals("imactv=paired-token", up.getHeader("Cookie"))
        assertEquals(1, JSONObject(up.body.readUtf8()).getJSONObject("params").getInt("direction"))
        provider.onAdjustVolume(AudioManager.ADJUST_LOWER)
        assertEquals(-1, JSONObject(commands.poll(3, TimeUnit.SECONDS)!!.body.readUtf8()).getJSONObject("params").getInt("direction"))
        provider.onAdjustVolume(AudioManager.ADJUST_SAME)
        assertNull(commands.poll(50, TimeUnit.MILLISECONDS))
        controller.get().onStartCommand(Intent().setAction("com.nexus.tv.TV_STOP"), 0, 2)
        assertEquals(false, TvVolumeService.status()["enabled"])
        assertFalse(session.isActive)
        assertFalse(TvVolumeService.handleKey(KeyEvent(KeyEvent.ACTION_DOWN, KeyEvent.KEYCODE_VOLUME_UP)))
    }

    @Test fun revokedPairingStopsBackgroundSessionAndReportsReason() {
        revoked = true
        start()
        val deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(4)
        while (TvVolumeService.status()["enabled"] == true && System.nanoTime() < deadline) {
            shadowOf(Looper.getMainLooper()).idle()
            Thread.sleep(10)
        }
        assertEquals(false, TvVolumeService.status()["enabled"])
        assertTrue(TvVolumeService.status()["error"].toString().contains("Pair with the TV again"))
    }

    @Test fun activityKeysUseSameServiceAndDoNotChangePhoneVolume() {
        start()
        assertTrue(TvVolumeService.handleKey(KeyEvent(KeyEvent.ACTION_DOWN, KeyEvent.KEYCODE_VOLUME_DOWN)))
        assertEquals(-1, JSONObject(commands.poll(3, TimeUnit.SECONDS)!!.body.readUtf8()).getJSONObject("params").getInt("direction"))
        assertTrue(TvVolumeService.handleKey(KeyEvent(KeyEvent.ACTION_UP, KeyEvent.KEYCODE_VOLUME_DOWN)))
        assertFalse(TvVolumeService.handleKey(KeyEvent(KeyEvent.ACTION_DOWN, KeyEvent.KEYCODE_BACK)))
        assertNull(commands.poll(50, TimeUnit.MILLISECONDS))
    }

    @Test fun nativeMediaActionsPreservePlayerAndYoutubeFunctionsUsingFreshState() {
        start()
        controller.get().mediaAction("pause")
        val pause = JSONObject(commands.poll(3, TimeUnit.SECONDS)!!.body.readUtf8())
        assertEquals("set_paused", pause.getString("action"))
        assertTrue(pause.getJSONObject("params").getBoolean("paused"))
        controller.get().mediaAction("forward")
        assertEquals(40.0, JSONObject(commands.poll(3, TimeUnit.SECONDS)!!.body.readUtf8()).getJSONObject("params").getDouble("seconds"), 0.01)
        tvState = """{"volume":40,"playback":{"active":false},"youtube_now":{"status":"Paused","title":"Clip","length":100,"position":12}}"""
        controller.get().mediaAction("play")
        val play = JSONObject(commands.poll(3, TimeUnit.SECONDS)!!.body.readUtf8())
        assertEquals("youtube_media", play.getString("action"))
        assertEquals("play", play.getJSONObject("params").getString("cmd"))
        controller.get().mediaAction("seek", 22.0)
        val seek = JSONObject(commands.poll(3, TimeUnit.SECONDS)!!.body.readUtf8()).getJSONObject("params")
        assertEquals("set_position", seek.getString("cmd"))
        assertEquals(22.0, seek.getDouble("position"), 0.01)
    }

    @Test fun pausedPlayerTakesPriorityOverBackgroundYoutube() {
        tvState = """{"volume":40,"media":{"name":"Current film"},"playback":{"active":true,"paused":true,"title":"File.mkv"},"youtube_now":{"status":"Playing","title":"Background clip"}}"""
        start()
        val deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(3)
        while (field("title") != "Current film" && System.nanoTime() < deadline) {
            shadowOf(Looper.getMainLooper()).idle()
            Thread.sleep(10)
        }
        assertEquals("Current film", field("title"))
        assertEquals(true, field("paused"))
    }
}
