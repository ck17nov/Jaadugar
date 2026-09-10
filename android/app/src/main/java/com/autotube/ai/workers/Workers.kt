package com.autotube.ai.workers

import android.app.NotificationChannel
import android.app.PendingIntent
import android.app.NotificationManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import com.autotube.ai.AutoTubeApp
import com.autotube.ai.MainActivity
import com.autotube.ai.R
import com.autotube.ai.data.remote.AutomationRequestDto
import java.util.concurrent.TimeUnit

/**
 * Background work (spec section 2).
 *
 * The phone is an orchestrator, not a renderer: these workers only talk HTTP.
 * That is what makes them survivable under Doze and Android's background
 * restrictions - each run is short, network-bound and idempotent.
 *
 * We deliberately do NOT use a long-running foreground service: the spec
 * requires not assuming one can run indefinitely, and WorkManager already
 * guarantees execution across process death and reboot.
 */

/** Polls job status so the dashboard is current when the user opens the app. */
class SyncWorker(appContext: Context, params: WorkerParameters) :
    CoroutineWorker(appContext, params) {

    override suspend fun doWork(): Result {
        val app = applicationContext as AutoTubeApp
        val repo = app.repository
        if (!app.secureStore.isConfigured) {
            // Nothing to sync until the backend is set up; do not burn retries.
            return Result.success()
        }

        val refreshed = repo.refreshJobs()
        if (refreshed.isFailure) {
            // Transient network problems are the common case -> retry with the
            // backoff configured on the request.
            return if (runAttemptCount < MAX_ATTEMPTS) Result.retry() else Result.success()
        }

        // Keep the local automation rows warm. AutomationWorker rebuilds each
        // recurring request from its Room row, and the schema migration is
        // destructive, so without this an upgrade leaves the table empty
        // until the user happens to open the Schedule tab.
        runCatching { repo.syncAutomations() }

        notifyIfNeeded(app)
        repo.prune()
        return Result.success()
    }

    /** Surface anything waiting on the user (spec section 24). */
    private suspend fun notifyIfNeeded(app: AutoTubeApp) {
        val jobs = app.database.jobs()
        val approvals = runCatching {
            jobs.countByStatusOnce(STATUS_AWAITING_APPROVAL)
        }.getOrDefault(0)
        val failures = runCatching {
            jobs.countByStatusOnce(STATUS_FAILED)
        }.getOrDefault(0)

        // Only notify when the count CHANGES. It previously fired on every
        // 15-minute sync for anything sitting in AWAITING_APPROVAL, so a job
        // the user had already seen - or one created during testing before
        // they ever opened the app - produced a fresh "video ready" alert
        // every quarter of an hour.
        val lastSeen = app.secureStore.lastApprovalCount
        app.secureStore.lastApprovalCount = approvals

        if (approvals > 0 && approvals > lastSeen) {
            postNotification(
                app,
                title = "Video ready for approval",
                body = if (approvals == 1) "1 video is waiting for your review."
                else "$approvals videos are waiting for your review.",
                id = NOTIF_APPROVAL,
            )
        }
        // Change detection here too. Without it this fired on every
        // 15-minute sync for as long as a single failed job existed - the
        // exact bug that was fixed for the approval branch above, repeated
        // verbatim underneath it. A job that failed once produced ninety-six
        // identical alerts a day.
        val lastFailures = app.secureStore.lastFailureCount
        app.secureStore.lastFailureCount = failures

        if (failures > 0 && failures > lastFailures) {
            postNotification(
                app,
                title = "Automation problem",
                body = "$failures job(s) failed. Open Jaadugar for details.",
                id = NOTIF_FAILURE,
            )
        }
    }

    companion object {
        const val NAME = "autotube-sync"
        const val MAX_ATTEMPTS = 4
        const val STATUS_AWAITING_APPROVAL = "AWAITING_APPROVAL"
        const val STATUS_FAILED = "FAILED"
        const val NOTIF_APPROVAL = 4101
        const val NOTIF_FAILURE = 4102
    }
}

/** Fires a configured automation on its schedule. */
class AutomationWorker(appContext: Context, params: WorkerParameters) :
    CoroutineWorker(appContext, params) {

    override suspend fun doWork(): Result {
        val app = applicationContext as AutoTubeApp
        if (!app.secureStore.isConfigured) return Result.success()

        val automationId = inputData.getString(KEY_AUTOMATION_ID)
        // "Specific days": a daily worker that does nothing on the other days.
        val allowedDays = inputData.getIntArray(KEY_DAYS) ?: IntArray(0)
        if (allowedDays.isNotEmpty()) {
            // java.time DayOfWeek is 1=Monday; the app stores 0=Monday.
            val today = java.time.LocalDate.now().dayOfWeek.value - 1
            if (today !in allowedDays) {
                return Result.success()
            }
        }
        var automation = automationId?.let { app.database.automations().byId(it) }

        // A MISSING ROW IS NOT PROOF THE AUTOMATION WAS DELETED.
        //
        // Room's migration is destructive, so an app upgrade empties this
        // table - and the self-cancel below then killed every recurring
        // automation the user had, permanently and without a word. Ask the
        // backend, which is where automations actually live, before
        // concluding anything.
        if (automation == null && automationId != null) {
            val restored = app.repository.syncAutomations()
            if (restored.isFailure) {
                // Could not reach the backend: "unreachable" must never be
                // read as "deleted". Retry, and leave the schedule alone.
                return if (runAttemptCount < 3) Result.retry() else Result.success()
            }
            automation = app.database.automations().byId(automationId)
            if (automation != null) {
                app.repository.logEvent(
                    "AUTOMATION",
                    "restored this automation from the backend after an " +
                        "upgrade emptied the local table")
            }
        }

        // Self-cancel when the automation is genuinely gone or switched off.
        //
        // Returning success alone left the periodic work in place, so a
        // stopped daily automation went on waking the phone every 24 hours
        // for ever - doing nothing, but keeping a schedule the user believed
        // they had cancelled.
        if (automation == null || !automation.enabled) {
            if (automationId != null) {
                WorkScheduler.cancelAutomation(applicationContext, automationId)
            }
            return Result.success()
        }

        val request = AutomationRequestDto(
            niche = automation.niche,
            audience = automation.audience,
            language = automation.language,
            videoFormat = automation.videoFormat,
            durationSeconds = automation.durationSeconds,
            voiceGender = automation.voiceGender,
            captionLanguage = automation.captionLanguage,
            captionStyle = automation.captionStyle,
            publishMode = automation.publishMode,
            channelId = automation.channelId,
            scriptSource = automation.scriptSource,
            nicheGroup = automation.nicheGroup,
            style = automation.style,
            count = 1,
            mode = automation.mode,
            frequency = automation.frequency,
            days = automation.days,
            uploadTime = automation.uploadTime,
            timezone = automation.timezone,
            madeForKids = automation.madeForKids,
            minQualityScore = automation.minQualityScore,
        )
        val result = app.repository.startAutomation(request)
        return if (result.isSuccess) {
            Result.success()
        } else if (runAttemptCount < 3) {
            Result.retry()
        } else {
            app.repository.logEvent("AUTOMATION", "failed to queue after retries")
            Result.success()
        }
    }

    companion object {
        const val KEY_AUTOMATION_ID = "automation_id"
        const val KEY_DAYS = "allowed_days"
        fun nameFor(automationId: String) = "autotube-automation-$automationId"

        /** Tag used to cancel a schedule even if the unique name has drifted. */
        fun tagFor(automationId: String) = "autotube-auto-tag-$automationId"
    }
}

// --------------------------------------------------------------------------
object WorkScheduler {

    private val networkConstraints = Constraints.Builder()
        .setRequiredNetworkType(NetworkType.CONNECTED)
        .build()

    /** Periodic status sync. 15 minutes is WorkManager's minimum interval. */
    fun scheduleSync(context: Context) {
        val request = PeriodicWorkRequestBuilder<SyncWorker>(15, TimeUnit.MINUTES)
            .setConstraints(networkConstraints)
            .setBackoffCriteria(
                androidx.work.BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS)
            .build()
        WorkManager.getInstance(context).enqueueUniquePeriodicWork(
            SyncWorker.NAME, ExistingPeriodicWorkPolicy.KEEP, request)
    }

    /**
     * Cancel periodic work whose worker class this build no longer contains.
     *
     * Enqueued periodic work lives in WorkManager's own database, not in the
     * APK, so it survives an upgrade that deletes the worker. WorkManager then
     * wakes up every 12 hours, fails to instantiate the missing class, logs
     * the failure and retries - forever. The analytics collector was removed
     * because own-channel numbers are already in YouTube Studio and were not
     * worth the API quota; this clears its leftover schedule from installs
     * that had it.
     */
    fun cancelRetiredWork(context: Context) {
        WorkManager.getInstance(context).cancelUniqueWork(RETIRED_ANALYTICS_WORK)
    }

    private const val RETIRED_ANALYTICS_WORK = "autotube-analytics"

    fun syncNow(context: Context) {
        val request = OneTimeWorkRequestBuilder<SyncWorker>()
            .setConstraints(networkConstraints)
            .build()
        WorkManager.getInstance(context).enqueueUniqueWork(
            "${SyncWorker.NAME}-now", ExistingWorkPolicy.REPLACE, request)
    }

    /**
     * Schedule a recurring automation.
     *
     * Content is generated AHEAD of the publish time (spec section 20): the
     * worker runs `leadHours` before the slot, and the backend sets YouTube's
     * own `publishAt`, so the phone does not need to be online at 8 PM.
     */
    fun scheduleAutomation(
        context: Context,
        automationId: String,
        intervalHours: Long,
        initialDelayMinutes: Long,
        days: List<Int> = emptyList(),
    ) {
        val request = PeriodicWorkRequestBuilder<AutomationWorker>(
            intervalHours.coerceAtLeast(1), TimeUnit.HOURS
        )
            .setConstraints(networkConstraints)
            .setInitialDelay(initialDelayMinutes.coerceAtLeast(0), TimeUnit.MINUTES)
            .setInputData(workDataOf(
                AutomationWorker.KEY_AUTOMATION_ID to automationId,
                // Which weekdays this automation is allowed to run on, 0=Mon.
                // Empty means every day. The worker checks it, because
                // "specific days" previously fell through to a 24-hour
                // interval and made a video every single day.
                AutomationWorker.KEY_DAYS to days.toIntArray(),
            ))
            .addTag(AutomationWorker.tagFor(automationId))
            .setBackoffCriteria(
                androidx.work.BackoffPolicy.EXPONENTIAL, 5, TimeUnit.MINUTES)
            .build()
        WorkManager.getInstance(context).enqueueUniquePeriodicWork(
            AutomationWorker.nameFor(automationId),
            ExistingPeriodicWorkPolicy.UPDATE,
            request,
        )
    }

    fun cancelAutomation(context: Context, automationId: String) {
        val manager = WorkManager.getInstance(context)
        manager.cancelUniqueWork(AutomationWorker.nameFor(automationId))
        // Also cancel by TAG.
        //
        // The unique-work name is derived from the automation id the backend
        // returned the FIRST time, while every later run returns a new id and
        // Room accumulates rows - so the name and the id can drift apart and
        // cancelUniqueWork alone would miss the schedule that is actually
        // running. The tag is attached in scheduleAutomation below.
        manager.cancelAllWorkByTag(AutomationWorker.tagFor(automationId))
    }
}

// --------------------------------------------------------------------------
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action == Intent.ACTION_BOOT_COMPLETED ||
            intent.action == Intent.ACTION_MY_PACKAGE_REPLACED
        ) {
            // Periodic work survives reboot on its own, but re-arming is cheap
            // and covers the case where the app was updated (spec section 22).
            WorkScheduler.scheduleSync(context)
        }
    }
}

// --------------------------------------------------------------------------
internal const val CHANNEL_ID = "autotube_status"

fun ensureNotificationChannel(context: Context) {
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
        val channel = NotificationChannel(
            CHANNEL_ID,
            "Automation status",
            NotificationManager.IMPORTANCE_DEFAULT,
        ).apply {
            description = "Approvals, failures and publishing updates."
        }
        context.getSystemService(NotificationManager::class.java)
            ?.createNotificationChannel(channel)
    }
}

fun postNotification(context: Context, title: String, body: String, id: Int) {
    // POST_NOTIFICATIONS is a runtime permission from Android 13; posting
    // without it is a silent no-op, so check rather than assume.
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
        val granted = context.checkSelfPermission(
            android.Manifest.permission.POST_NOTIFICATIONS
        ) == PackageManager.PERMISSION_GRANTED
        if (!granted) return
    }
    ensureNotificationChannel(context)

    // Without a content intent, tapping the notification does NOTHING - it
    // just sits there. That was the behaviour: an alert saying a video was
    // ready, which could not take you to it.
    val launch = Intent(context, MainActivity::class.java).apply {
        flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
    }
    val pending = PendingIntent.getActivity(
        context, id, launch,
        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
    )

    val notification = NotificationCompat.Builder(context, CHANNEL_ID)
        .setSmallIcon(R.drawable.ic_notification)
        .setContentTitle(title)
        .setContentText(body)
        .setContentIntent(pending)
        .setAutoCancel(true)
        .setPriority(NotificationCompat.PRIORITY_DEFAULT)
        // An updated count reuses the same notification id, so it should
        // refresh the text silently rather than buzz the phone again.
        .setOnlyAlertOnce(true)
        .build()
    runCatching {
        NotificationManagerCompat.from(context).notify(id, notification)
    }
}
