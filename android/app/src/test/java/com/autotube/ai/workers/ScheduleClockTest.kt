package com.autotube.ai.workers

import java.time.ZoneId
import java.time.ZonedDateTime
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The daily slot must be the time the operator chose.
 *
 * Reported on the live channel: four automations set for 16:00, 17:00, 18:00
 * and 19:00 produced four videos inside the same minute at 23:57, which
 * overran the Pacific quota day and failed the runs after it. The cause was
 * an initial delay of `intervalHours * 60` - a full day from whenever the
 * re-arm pass ran, so every automation shared one arbitrary slot.
 *
 * A real JUnit test, not a string assertion over the source: this is pure
 * java.time arithmetic and there is no excuse for pinning it any other way.
 */
class ScheduleClockTest {

    private val ist = ZoneId.of("Asia/Kolkata")

    private fun at(hour: Int, minute: Int): ZonedDateTime =
        ZonedDateTime.of(2026, 9, 18, hour, minute, 0, 0, ist)

    @Test
    fun `a slot later today is minutes away, not a day`() {
        val mins = ScheduleClock.minutesUntilNext("17:00", "Asia/Kolkata",
                                                  at(16, 30))
        assertEquals(30L, mins)
    }

    @Test
    fun `four staggered automations get four different delays`() {
        // THE REGRESSION. Armed in one pass, they must not converge.
        val now = at(12, 0)
        val delays = listOf("16:00", "17:00", "18:00", "19:00").map {
            ScheduleClock.minutesUntilNext(it, "Asia/Kolkata", now)
        }
        assertEquals(listOf(240L, 300L, 360L, 420L), delays)
        assertEquals(4, delays.toSet().size)
    }

    @Test
    fun `a slot already past today is tomorrow, not immediate`() {
        val mins = ScheduleClock.minutesUntilNext("16:00", "Asia/Kolkata",
                                                  at(23, 0))
        assertEquals(17L * 60L, mins)
    }

    @Test
    fun `exactly at the slot means tomorrow`() {
        // Today's run is in flight or done. Re-arming for "now" would make a
        // second video immediately, which is the duplicate the old full-day
        // delay was there to avoid.
        val mins = ScheduleClock.minutesUntilNext("17:00", "Asia/Kolkata",
                                                  at(17, 0))
        assertEquals(24L * 60L, mins)
    }

    @Test
    fun `the operator's zone is used, not the phone's`() {
        // Same instant, two automations, different zones: the delays must
        // differ by the offset.
        val instant = at(12, 0)
        val india = ScheduleClock.minutesUntilNext("16:00", "Asia/Kolkata",
                                                   instant)
        val pacific = ScheduleClock.minutesUntilNext("16:00",
                                                     "America/Los_Angeles",
                                                     instant)
        assertTrue("a zone must change the answer", india != pacific)
    }

    @Test
    fun `an unreadable time reports -1 so the caller can fall back`() {
        assertEquals(-1L, ScheduleClock.minutesUntilNext("", "Asia/Kolkata"))
        assertEquals(-1L, ScheduleClock.minutesUntilNext("teatime",
                                                        "Asia/Kolkata"))
        assertEquals(-1L, ScheduleClock.minutesUntilNext("25:00",
                                                        "Asia/Kolkata"))
        assertEquals(-1L, ScheduleClock.minutesUntilNext("16:99",
                                                        "Asia/Kolkata"))
    }

    @Test
    fun `an unknown zone falls back instead of throwing`() {
        // This runs inside a worker. An exception would take every
        // automation's re-arm with it, not just the one with the bad zone.
        val mins = ScheduleClock.minutesUntilNext("16:00", "Mars/Olympus")
        assertTrue(mins in 0..(24L * 60L))
    }

    @Test
    fun `a bare hour is accepted as the top of that hour`() {
        assertEquals(60L, ScheduleClock.minutesUntilNext("17", "Asia/Kolkata",
                                                         at(16, 0)))
    }
}
