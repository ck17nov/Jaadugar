package com.autotube.ai.workers

import java.time.Duration
import java.time.ZoneId
import java.time.ZonedDateTime

/**
 * When a recurring automation's next slot falls.
 *
 * THE SCHEDULE USED TO IGNORE THE TIME THE OPERATOR CHOSE. Both callers
 * passed `intervalHours * 60` as the initial delay, which is 24 hours from
 * whenever the code happened to run - so a daily automation fired at the
 * minute it was created or last re-armed, never at its `uploadTime`. And
 * because `SyncWorker.rearmSchedules` walks every automation in one pass,
 * automations set for 16:00, 17:00, 18:00 and 19:00 were all armed within
 * the same second and then produced together: measured on the live channel
 * as four uploads inside the same minute, which overran the Pacific quota
 * day and failed the runs that followed at the upload.
 *
 * Deliberately in its own file with no Android imports. `WorkScheduler`
 * builds a `Constraints` at object initialisation, so a plain JVM unit test
 * that so much as mentions it dies on NoClassDefFoundError - which is how
 * this maths would have gone untested.
 */
object ScheduleClock {

    /**
     * Minutes from now until the next `HH:mm` in `timezone`, or -1 when
     * either cannot be read.
     *
     * -1 rather than an exception or a guess: the callers fall back to the
     * old interval-based delay, which is wrong about the time of day but
     * still produces a video. Failing the re-arm would leave an automation
     * with no schedule at all.
     *
     * `now` is injectable so this is testable without waiting for a clock.
     */
    fun minutesUntilNext(
        uploadTime: String,
        timezone: String,
        now: ZonedDateTime? = null,
    ): Long {
        val parts = uploadTime.trim().split(":")
        val hour = parts.getOrNull(0)?.trim()?.toIntOrNull() ?: return -1L
        val minute = parts.getOrNull(1)?.trim()?.toIntOrNull() ?: 0
        if (hour !in 0..23 || minute !in 0..59) return -1L

        // An unknown zone must not throw: this runs inside a worker, and an
        // exception here would take the whole re-arm pass with it - every
        // automation on the phone, not just the one with the bad zone.
        val zone = runCatching { ZoneId.of(timezone) }
            .getOrElse { ZoneId.systemDefault() }
        val from = now?.withZoneSameInstant(zone) ?: ZonedDateTime.now(zone)
        var next = from.withHour(hour).withMinute(minute)
            .withSecond(0).withNano(0)
        // `!isAfter` rather than `isBefore`: exactly at the slot, today's run
        // is either in flight or already done, so the next one is tomorrow.
        if (!next.isAfter(from)) next = next.plusDays(1)
        return Duration.between(from, next).toMinutes()
    }
}
