package com.autotube.ai.ui.screens

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.TextButton
import androidx.compose.material3.Text
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.autotube.ai.data.local.JobEntity
import com.autotube.ai.ui.components.BannerTone
import com.autotube.ai.ui.components.InfoBanner
import com.autotube.ai.data.remote.AutomationSummaryDto
import com.autotube.ai.ui.components.EmptyState
import com.autotube.ai.ui.components.SectionTitle
import com.autotube.ai.ui.components.StatusChip
import com.autotube.ai.ui.vm.JobViewModel
import com.autotube.ai.ui.vm.ScheduleViewModel
import com.autotube.ai.ui.vm.appViewModel
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone

private val STATUS_FILTERS = listOf(
    "ALL", "AWAITING_APPROVAL", "READY", "SCHEDULED", "PUBLISHED", "FAILED",
)

/** Screen 4: generated ideas, scripts and their scores. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun ContentScreen(onOpenJob: (String) -> Unit) {
    val vm: JobViewModel = appViewModel()
    val jobs by vm.jobs.collectAsStateWithLifecycle()
    var filter by remember { mutableStateOf("ALL") }

    val visible = remember(jobs, filter) {
        if (filter == "ALL") jobs else jobs.filter { it.status == filter }
    }

    LazyColumn(
        Modifier.fillMaxWidth().padding(horizontal = 16.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        item {
            Text(
                "Content",
                style = MaterialTheme.typography.displaySmall,
                modifier = Modifier.padding(top = 16.dp),
            )
        }
        item {
            FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                STATUS_FILTERS.forEach { option ->
                    FilterChip(
                        selected = filter == option,
                        onClick = { filter = option },
                        label = {
                            Text(
                                option.replace('_', ' '),
                                style = MaterialTheme.typography.labelSmall,
                            )
                        },
                    )
                }
            }
        }

        if (visible.isEmpty()) {
            item {
                EmptyState(
                    title = "Nothing here",
                    body = "No jobs match this filter yet.",
                )
            }
        } else {
            items(visible, key = { it.jobId }) { job ->
                JobRow(job = job, onClick = { onOpenJob(job.jobId) })
            }
        }
        item { Spacer(Modifier.height(24.dp)) }
    }
}

/** Screen 6: the publishing calendar / list. */
@Composable
fun SchedulerScreen(onOpenJob: (String) -> Unit) {
    val vm: ScheduleViewModel = appViewModel()
    val jobs by vm.jobs.collectAsStateWithLifecycle()
    val automations by vm.automations.collectAsStateWithLifecycle()
    val message by vm.message.collectAsStateWithLifecycle()
    val loaded by vm.loaded.collectAsStateWithLifecycle()
    LaunchedEffect(Unit) { vm.refresh() }

    val scheduled = remember(jobs) {
        jobs.filter { it.scheduledFor.isNotBlank() }
            .sortedBy { it.scheduledFor }
    }
    val grouped = remember(scheduled, vm.store.timezone) {
        scheduled.groupBy { localDay(it.scheduledFor, vm.store.timezone) }
    }

    LazyColumn(
        Modifier.fillMaxWidth().padding(horizontal = 16.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        item {
            Column(Modifier.padding(top = 16.dp)) {
                Text("Schedule", style = MaterialTheme.typography.displaySmall)
                Text(
                    "Times shown in ${vm.store.timezone}",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }

        message?.let { msg ->
            item(key = "schedule-message") {
                InfoBanner(
                    text = msg.text,
                    tone = if (msg.isError) BannerTone.Error else BannerTone.Success,
                    actionLabel = "Dismiss",
                    onAction = { vm.clearMessage() },
                )
            }
        }

        // ---- recurring automations --------------------------------------
        //
        // Previously invisible: automations lived only in the phone's own
        // database and nothing displayed them, so a daily automation could be
        // started and then never seen again - and there was no way to stop one
        // except by catching a video it had already begun.
        item(key = "automations-title") {
            SectionTitle(
                if (automations.isEmpty()) "Recurring automations"
                else "Recurring automations (${automations.count { it.enabled }})"
            )
        }
        if (automations.isEmpty()) {
            item(key = "automations-empty") {
                Text(
                    if (!loaded)
                        "Could not load automations from the backend - this is " +
                            "not the same as having none."
                    else
                        "None. An automation set to daily, weekly or specific " +
                            "days will appear here with a Stop button.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        } else {
            items(automations, key = { "auto-${it.id}" }) { automation ->
                AutomationRow(
                    automation = automation,
                    onStop = { vm.stopAutomation(automation.id) },
                )
            }
        }

        item(key = "scheduled-title") { SectionTitle("Scheduled videos") }

        if (grouped.isEmpty()) {
            item {
                EmptyState(
                    title = "Nothing scheduled",
                    body = "Videos scheduled through YouTube's publishAt will " +
                        "appear here. Your phone does not need to be online at " +
                        "the publish time.",
                )
            }
        }

        grouped.forEach { (day, dayJobs) ->
            item(key = "day-$day") { SectionTitle(day) }
            items(dayJobs, key = { it.jobId }) { job ->
                ScheduleRow(
                    job = job,
                    timezone = vm.store.timezone,
                    onClick = { onOpenJob(job.jobId) },
                )
            }
        }

        item { Spacer(Modifier.height(24.dp)) }
    }
}

@Composable
private fun ScheduleRow(job: JobEntity, timezone: String, onClick: () -> Unit) {
    Card(
        // onClick was passed in and never used, so every row on this tab was
        // dead - tapping a scheduled video did nothing while the identical
        // rows on Dashboard and Content opened the preview.
        modifier = Modifier.fillMaxWidth().clickable(onClick = onClick),
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surfaceVariant
        ),
        shape = RoundedCornerShape(12.dp),
    ) {
        Row(
            Modifier.padding(12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(Modifier.width(66.dp)) {
                Text(
                    localTime(job.scheduledFor, timezone),
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.Bold,
                )
                Text(
                    "${job.duration.toInt()}s",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            Spacer(Modifier.width(10.dp))
            Column(Modifier.weight(1f)) {
                Text(
                    job.title.ifBlank { job.niche },
                    style = MaterialTheme.typography.bodyMedium,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                )
                if (job.youtubeVideoId.isNotBlank()) {
                    Text(
                        "youtu.be/${job.youtubeVideoId}",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
            Spacer(Modifier.width(8.dp))
            StatusChip(job.status)
        }
    }
}

// --------------------------------------------------------------------------
// The backend returns RFC3339 UTC; the user thinks in their own timezone.
private fun parseUtc(value: String): Date? = runCatching {
    val fmt = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'", Locale.US).apply {
        timeZone = TimeZone.getTimeZone("UTC")
    }
    fmt.parse(value)
}.getOrNull()

private fun localDay(utc: String, timezone: String): String {
    val date = parseUtc(utc) ?: return "Unscheduled"
    val fmt = SimpleDateFormat("EEE d MMM", Locale.getDefault()).apply {
        timeZone = TimeZone.getTimeZone(timezone)
    }
    return fmt.format(date)
}

private fun localTime(utc: String, timezone: String): String {
    val date = parseUtc(utc) ?: return "--:--"
    val fmt = SimpleDateFormat("HH:mm", Locale.getDefault()).apply {
        timeZone = TimeZone.getTimeZone(timezone)
    }
    return fmt.format(date)
}

/**
 * One recurring automation, with the one control that matters.
 *
 * Stop here ends the whole automation - the backend drops queued runs and
 * records the cancellation, and the phone's WorkManager schedule is torn down
 * so tomorrow's video is never made. That is different from stopping a single
 * job on the Dashboard, which only abandons the run in progress.
 */
@Composable
private fun AutomationRow(
    automation: AutomationSummaryDto,
    onStop: () -> Unit,
) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surfaceVariant
        ),
        shape = RoundedCornerShape(12.dp),
    ) {
        Column(Modifier.padding(12.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    if (automation.topicRotate) {
                        "Auto - all ${automation.topicCount} " +
                            "${automation.groupLabel} topics"
                    } else {
                        automation.niche.ifBlank { "(no niche)" }
                    },
                    style = MaterialTheme.typography.bodyMedium,
                    fontWeight = FontWeight.Medium,
                    modifier = Modifier.weight(1f),
                )
                if (automation.running) StatusChip("RUNNING")
                else if (!automation.enabled) StatusChip("STOPPED")
            }
            Spacer(Modifier.height(4.dp))
            Text(
                buildString {
                    append(
                        when (automation.frequency) {
                            "daily" -> "Every day"
                            "weekly" -> "Every week"
                            "days" -> "On chosen days"
                            else -> "One run"
                        }
                    )
                    if (automation.uploadTime.isNotBlank()) {
                        append(" at ${automation.uploadTime}")
                    }
                    if (automation.videoFormat.isNotBlank()) {
                        append(" - ${automation.videoFormat}")
                    }
                    if (automation.language.isNotBlank()) {
                        append(" - ${automation.language}")
                    }
                    append(" - ${automation.videosMade} made")
                    // `last`, not `next`: a topic with no reviewed script
                    // left is skipped for that lap, so the next name in the
                    // list is not necessarily what will run.
                    if (automation.topicRotate &&
                        automation.lastTopic.isNotBlank()) {
                        append(" - last: ${automation.lastTopic}")
                    }
                },
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            // WHERE it publishes, on its own line.
            //
            // Two daily automations - one kids, one finance - read as
            // identical rows without this, and the destination is the whole
            // reason for having both.
            if (automation.channelTitle.isNotBlank() ||
                automation.groupLabel.isNotBlank()) {
                Spacer(Modifier.height(2.dp))
                Text(
                    buildString {
                        if (automation.groupLabel.isNotBlank()) {
                            append(automation.groupLabel)
                            append(" -> ")
                        }
                        append(automation.channelTitle.ifBlank { "default channel" })
                        if (automation.channelIsDefault) append(" (default)")
                    },
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.primary,
                )
            }
            if (automation.enabled) {
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
                    TextButton(onClick = onStop) { Text("Stop automation") }
                }
            }
        }
    }
}
