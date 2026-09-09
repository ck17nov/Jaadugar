package com.autotube.ai.ui.screens

import android.app.Activity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Slider
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.autotube.ai.auth.YouTubeAuthManager
import com.autotube.ai.data.remote.NicheGroupDto
import com.autotube.ai.data.remote.YouTubeAccountDto
import com.autotube.ai.ui.components.BannerTone
import com.autotube.ai.ui.components.InfoBanner
import com.autotube.ai.ui.components.LoadingRow
import com.autotube.ai.ui.components.SectionTitle
import com.autotube.ai.ui.vm.SettingsViewModel
import com.autotube.ai.ui.vm.appViewModel
import kotlin.math.roundToInt
import com.autotube.ai.ui.components.LabeledDropdown

// One zone on purpose - see the note where it is displayed.
private val TIMEZONES = listOf("Asia/Kolkata")

@Composable
fun SettingsScreen() {
    val vm: SettingsViewModel = appViewModel()
    val store = vm.store
    val health by vm.health.collectAsStateWithLifecycle()
    val youtube by vm.youtube.collectAsStateWithLifecycle()
    val accounts by vm.accounts.collectAsStateWithLifecycle()
    val groups by vm.groups.collectAsStateWithLifecycle()
    val defaultChannel by vm.defaultChannel.collectAsStateWithLifecycle()
    val busy by vm.busy.collectAsStateWithLifecycle()
    val message by vm.message.collectAsStateWithLifecycle()
    val context = LocalContext.current

    // Nothing here writes to storage until Save.
    //
    // Every field used to commit on each keystroke, so brushing a slider or a
    // dropdown changed a live setting with no way back - which is exactly what
    // happened. The drafts below are the only source of truth for the controls
    // while editing, and rememberSaveable is load-bearing now that they are
    // not committed immediately: a rotation mid-edit would otherwise re-seed
    // from storage and silently discard the changes.
    var editing by rememberSaveable { mutableStateOf(false) }
    var backendUrl by rememberSaveable { mutableStateOf(store.backendUrl) }
    var apiKey by rememberSaveable { mutableStateOf(store.apiKey) }
    var oauthClientId by rememberSaveable { mutableStateOf(store.oauthClientId) }
    var ytAccount by rememberSaveable { mutableStateOf(store.youtubeAccountEmail) }
    val timezone = store.timezone
    var threshold by rememberSaveable { mutableIntStateOf(store.qualityThreshold) }
    var autoApprove by rememberSaveable { mutableStateOf(store.autoApprove) }

    fun save() {
        store.backendUrl = backendUrl
        store.apiKey = apiKey
        store.oauthClientId = oauthClientId
        store.youtubeAccountEmail = ytAccount
        store.qualityThreshold = threshold
        store.autoApprove = autoApprove
        editing = false
    }

    fun cancel() {
        // Re-seed from storage so a half-finished edit leaves nothing behind.
        backendUrl = store.backendUrl
        apiKey = store.apiKey
        oauthClientId = store.oauthClientId
        ytAccount = store.youtubeAccountEmail
        threshold = store.qualityThreshold
        autoApprove = store.autoApprove
        editing = false
    }

    val authManager = remember { YouTubeAuthManager(context, store) }
    val signingSha1 = remember { YouTubeAuthManager.signingSha1(context) }
    DisposableEffect(Unit) { onDispose { authManager.dispose() } }

    val authLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { result ->
        if (result.resultCode == Activity.RESULT_OK || result.data != null) {
            authManager.handleResult(result.data) { token, error ->
                when {
                    token != null -> vm.sendRefreshToken(token)
                    error != null -> vm.reportAuthError(error)
                }
            }
        } else {
            vm.reportAuthError("Sign-in was cancelled.")
        }
    }

    LaunchedEffect(Unit) {
        if (store.isConfigured) {
            vm.testConnection()
            vm.refreshYouTube()
            vm.refreshAccounts()
            vm.refreshGroups()
        }
    }

    Column(
        Modifier
            .fillMaxWidth()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("Settings", style = MaterialTheme.typography.displaySmall,
                 modifier = Modifier.weight(1f))
            if (editing) {
                TextButton(onClick = { cancel() }) { Text("Cancel") }
                Button(onClick = { save() }) { Text("Save") }
            } else {
                Button(onClick = { editing = true }) { Text("Edit") }
            }
        }
        if (!editing) {
            Text(
                "Read-only. Tap Edit to change anything.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }

        message?.let { msg ->
            InfoBanner(
                text = msg.text,
                tone = if (msg.isError) BannerTone.Error else BannerTone.Success,
                actionLabel = "Dismiss",
                onAction = { vm.clearMessage() },
            )
        }
        if (busy) LoadingRow()

        // ---- backend ----------------------------------------------------
        if (store.inMemoryOnly) {
            InfoBanner(
                text = "Secure storage could not be opened, so the backend key " +
                    "and YouTube connection will not be remembered after you " +
                    "close the app. Re-enter them, or reinstall if it persists.",
                tone = BannerTone.Error,
            )
        }

        SectionTitle("Backend")
        OutlinedTextField(
            value = backendUrl,
            onValueChange = { backendUrl = it },
            enabled = editing,
            label = { Text("Backend URL") },
            placeholder = { Text("http://192.168.1.20:8099/") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        OutlinedTextField(
            value = apiKey,
            onValueChange = { apiKey = it },
            enabled = editing,
            label = { Text("Backend API key (AUTOTUBE_API_TOKEN)") },
            singleLine = true,
            visualTransformation = PasswordVisualTransformation(),
            modifier = Modifier.fillMaxWidth(),
        )
        Text(
            "Stored encrypted on this device using the Android Keystore. " +
                "HTTPS is required except on a local network address.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Button(onClick = { vm.testConnection() }, enabled = !busy) {
            Text("Test connection")
        }

        health?.let { h ->
            Card(
                colors = CardDefaults.cardColors(
                    containerColor = MaterialTheme.colorScheme.surfaceVariant
                ),
                shape = RoundedCornerShape(12.dp),
            ) {
                Column(Modifier.padding(12.dp)) {
                    SectionTitle("Backend services")
                    ServiceLine("ffmpeg", h.ffmpeg, if (h.ffmpeg) "installed" else "missing")
                    ServiceLine(
                        "LLM",
                        h.llmProviders.isNotEmpty(),
                        h.llmProviders.joinToString().ifBlank { "none configured" },
                    )
                    ServiceLine(
                        "Voice (TTS)",
                        h.ttsProviders.isNotEmpty(),
                        h.ttsProviders.joinToString().ifBlank { "none" },
                    )
                    ServiceLine(
                        "YouTube research",
                        h.researchConfigured,
                        if (h.researchConfigured) "API key set" else "no API key",
                    )
                    ServiceLine(
                        "Uploads",
                        h.uploadEnabled && !h.dryRun,
                        when {
                            h.dryRun -> "DRY RUN - artifacts only, nothing uploaded"
                            !h.uploadEnabled -> "disabled in backend config"
                            else -> "enabled"
                        },
                    )
                }
            }
        }

        // ---- YouTube account --------------------------------------------
        SectionTitle("YouTube account")
        OutlinedTextField(
            value = oauthClientId,
            onValueChange = { oauthClientId = it },
            enabled = editing,
            label = { Text("Android OAuth client ID") },
            placeholder = { Text("123456789012-abc123def.apps.googleusercontent.com") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        Text(
            "Create an Android OAuth client in Google Cloud (no client secret " +
                "needed - the app uses PKCE). See docs/YOUTUBE_SETUP.md.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        OutlinedTextField(
            value = ytAccount,
            onValueChange = { ytAccount = it },
            enabled = editing,
            label = { Text("Google account for the channel (optional)") },
            placeholder = { Text("name@gmail.com") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        Text(
            "Sign-in opens in Chrome, which otherwise uses whichever Google " +
                "account is its default - not necessarily the one that owns " +
                "the channel. Filling this in pre-selects the right account. " +
                "The account chooser is always shown, so you can correct it there.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )

        // The three values Google Cloud asks for, read off this build.
        //
        // Google answers a registration mismatch with an "invalid request"
        // page that names no field, so guessing any of these costs a full
        // round trip through the console. Showing them - and letting the user
        // copy them - is the difference between a two-minute setup and an
        // afternoon.
        Card(
            colors = CardDefaults.cardColors(
                containerColor = MaterialTheme.colorScheme.surfaceVariant
            ),
            shape = RoundedCornerShape(12.dp),
        ) {
            Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                SectionTitle("Register these in Google Cloud")
                CopyableValue("Package name", com.autotube.ai.BuildConfig.APPLICATION_ID)
                CopyableValue("SHA-1 certificate fingerprint", signingSha1)
                CopyableValue("Redirect URI", YouTubeAuthManager.redirectUri)
                Text(
                    "APIs & Services > Credentials > Create credentials > " +
                        "OAuth client ID > Android. The package name and SHA-1 " +
                        "must match exactly, and your Google account must be " +
                        "listed under OAuth consent screen > Test users.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(
                onClick = {
                    // Save first. The button was enabled from the DRAFT client
                    // id while YouTubeAuthManager reads the SAVED one, so
                    // pasting an id and tapping Connect refused with "Set the
                    // OAuth client ID in Settings first" while the field
                    // visibly contained it.
                    if (editing) save()
                    runCatching { authManager.launch(authLauncher) }
                        .onFailure {
                            vm.reportAuthError(it.message ?: "Cannot start sign-in")
                        }
                },
                enabled = oauthClientId.isNotBlank() && !busy,
            ) { Text(if (editing) "Save and connect" else "Connect YouTube") }
            OutlinedButton(onClick = { vm.refreshYouTube() }) { Text("Refresh") }
        }

        youtube?.let { yt ->
            Card(shape = RoundedCornerShape(12.dp)) {
                Column(Modifier.padding(12.dp)) {
                    // Reports WHERE the OAuth client came from.
                    //
                    // This used to show "missing on backend" in red whenever
                    // .env had no desktop client - which is the normal state
                    // when you connect from the phone, the supported path. It
                    // read as an error for a correct setup.
                    ServiceLine(
                        "YouTube connection",
                        yt.clientSource != "none",
                        when (yt.clientSource) {
                            "device" -> "connected from this phone"
                            "env" -> "using the backend's own OAuth client"
                            else -> "not connected - tap Connect YouTube"
                        },
                    )
                    ServiceLine(
                        "Can upload",
                        yt.authorized,
                        if (yt.authorized) "yes"
                        else "no - reconnect YouTube",
                    )
                    if (yt.channels.isEmpty() && yt.authorized) {
                        Text(
                            "Connected, but no channel came back yet. Tap Refresh.",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                    yt.channels.forEach { channel ->
                        Spacer(Modifier.height(6.dp))
                        Text(channel.title, style = MaterialTheme.typography.bodyMedium)
                        Text(
                            "${channel.subscribers} subscribers - " +
                                "${channel.videos} videos",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                    if (yt.error.isNotBlank()) {
                        Text(
                            yt.error,
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.error,
                        )
                    }
                }
            }
        }
        Text(
            "Your Google password is never seen or stored by this app. Only an " +
                "OAuth token is used, and it can be revoked from your Google " +
                "account at any time.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )

        // ---- brand channels ---------------------------------------------
        SectionTitle("Publishing channels")
        Text(
            "One entry per channel you have connected. A YouTube sign-in can " +
                "only act as the single channel you pick in Google's chooser, " +
                "so each brand channel under your account is added separately. " +
                "Give a channel its groups - Kids, Finance, Tech - and every " +
                "topic in those groups publishes there automatically.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )

        if (accounts.isEmpty()) {
            Text(
                "No channels connected yet. Tap Connect YouTube above.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }

        accounts.forEach { account ->
            ChannelCard(
                account = account,
                isDefault = account.channelId == defaultChannel,
                groups = groups,
                takenElsewhere = accounts
                    .filter { it.channelId != account.channelId }
                    .flatMap { it.niches }
                    .toSet(),
                onMakeDefault = { vm.makeDefault(account.channelId) },
                onToggleNiche = { niche ->
                    val next = if (niche in account.niches) {
                        account.niches - niche
                    } else {
                        account.niches + niche
                    }
                    vm.assignNiches(account.channelId, next)
                },
                onForget = { vm.forgetChannel(account.channelId) },
            )
        }

        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedButton(
                onClick = {
                    // Forces Google's chooser - see YouTubeAuthManager. Without
                    // it Google can silently re-issue a token for the channel
                    // already connected, and "Add channel" adds nothing.
                    runCatching { authManager.launch(authLauncher, addChannel = true) }
                        .onFailure {
                            vm.reportAuthError(it.message ?: "Cannot start sign-in")
                        }
                },
                enabled = oauthClientId.isNotBlank() && !busy,
            ) { Text("Add channel") }
            OutlinedButton(onClick = { vm.refreshAccounts() }) { Text("Reload") }
        }

        // No "default niche" or "default language" here any more. Asked for:
        // "from setting we can remove default niche and default language."
        //
        // They were a poor fit for how the screen is actually used. The
        // Create tab already remembers the last group, topic and language
        // across process death via rememberSaveable, so the defaults only
        // ever applied to a first run - and having the same choice in two
        // places invited them to disagree, with no indication which one won.

        // Timezone is fixed to Asia/Kolkata. It only affects when a scheduled
        // upload fires, and a list of switches for zones that will never be
        // used was clutter on an already long screen. Change the constant if
        // this ever needs to move.
        Text(
            "Timezone: $timezone",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )

        SectionTitle("Minimum quality score to publish: $threshold/100")
        Slider(
            enabled = editing,
            value = threshold.toFloat(),
            onValueChange = {
                threshold = it.roundToInt()
            },
            valueRange = 50f..95f,
            steps = 8,
        )
        Text(
            "The backend refuses to upload anything below this score. Lowering " +
                "it does not disable the hard blockers (silent audio, wrong " +
                "resolution, failed originality check).",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )

        Row(verticalAlignment = Alignment.CenterVertically) {
            Switch(
                checked = autoApprove,
                enabled = editing,
                onCheckedChange = { autoApprove = it },
            )
            Column(Modifier.padding(start = 12.dp)) {
                Text("Default to AUTO mode", style = MaterialTheme.typography.bodyMedium)
                Text(
                    "New automations skip manual approval. Off by default.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }

        // ---- storage / danger zone --------------------------------------
        SectionTitle("Storage")
        Text(
            "Rendered video stays on the backend. This app caches only job " +
                "metadata and streams previews on demand, to keep phone storage " +
                "use minimal.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        OutlinedButton(onClick = { vm.clearSecrets() }) {
            Text("Clear stored credentials")
        }

        Spacer(Modifier.height(32.dp))
    }
}

@Composable
private fun ServiceLine(label: String, ok: Boolean, detail: String) {
    Row(Modifier.fillMaxWidth().padding(vertical = 3.dp)) {
        Text(
            if (ok) "OK" else "--",
            style = MaterialTheme.typography.labelSmall,
            color = if (ok) MaterialTheme.colorScheme.tertiary
            else MaterialTheme.colorScheme.error,
            modifier = Modifier.padding(end = 8.dp),
        )
        Column {
            Text(label, style = MaterialTheme.typography.bodySmall)
            Text(
                detail,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

/**
 * A label with a selectable, copyable value. A 59-character SHA-1 cannot be
 * transcribed by hand onto a laptop without an error, so it goes to the
 * clipboard instead.
 */
@Composable
private fun CopyableValue(label: String, value: String) {
    val clipboard = LocalClipboardManager.current
    Row(
        Modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            Text(
                label,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Text(value, style = MaterialTheme.typography.bodyMedium)
        }
        TextButton(onClick = { clipboard.setText(AnnotatedString(value)) }) {
            Text("Copy")
        }
    }
}

/**
 * One connected brand channel: what it is, whether it is the default, and
 * which niches publish to it.
 *
 * The niche chips are the mapping the user asked for - "for kids niche I'll
 * post under kids channel and for finance under finance channel". A niche can
 * belong to only one channel, so a chip already claimed by another channel is
 * shown but disabled rather than hidden: seeing WHERE it went is the useful
 * information, and silently omitting it looks like a missing option.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun ChannelCard(
    account: YouTubeAccountDto,
    isDefault: Boolean,
    groups: List<NicheGroupDto>,
    takenElsewhere: Set<String>,
    onMakeDefault: () -> Unit,
    onToggleNiche: (String) -> Unit,
    onForget: () -> Unit,
) {
    var confirmForget by remember { mutableStateOf(false) }
    var showNiches by rememberSaveable(account.channelId) { mutableStateOf(false) }

    Card(shape = RoundedCornerShape(12.dp), modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column(Modifier.weight(1f)) {
                    Text(
                        account.title.ifBlank { "Identifying channel..." },
                        style = MaterialTheme.typography.bodyLarge,
                    )
                    Text(
                        if (isDefault) "Default - used when a niche has no channel"
                        else account.channelId,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                if (isDefault) {
                    AssistChip(onClick = {}, label = { Text("Default") })
                } else {
                    TextButton(onClick = onMakeDefault) { Text("Make default") }
                }
            }

            // Read the mapping back in the user's own terms: group labels
            // where a whole group is mapped, and bare topic names for any
            // legacy per-topic mapping that predates groups.
            val mappedGroups = groups.filter { g ->
                account.niches.any { it.equals(g.key, ignoreCase = true) }
            }
            val loneTopics = account.niches.filter { n ->
                groups.none { it.key.equals(n, ignoreCase = true) }
            }
            val summary = (mappedGroups.map { it.label } + loneTopics)
            Text(
                if (summary.isEmpty()) "No groups assigned"
                else "Publishes: " + summary.joinToString(", "),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )

            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                TextButton(onClick = { showNiches = !showNiches }) {
                    Text(if (showNiches) "Done" else "Choose groups")
                }
                TextButton(onClick = { confirmForget = true }) { Text("Remove") }
            }

            if (showNiches) {
                // SIX chips, not twenty-one.
                //
                // This listed every topic, so the kids channel needed nine
                // ticks and missing one sent that topic silently to the
                // default channel. One chip per group covers the group.
                FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    groups.forEach { group ->
                        val mine = account.niches.any {
                            it.equals(group.key, ignoreCase = true)
                        }
                        FilterChip(
                            selected = mine,
                            enabled = mine || group.key !in takenElsewhere,
                            onClick = { onToggleNiche(group.key) },
                            label = {
                                Text("${group.label} (${group.topics.size})",
                                     style = MaterialTheme.typography.bodySmall)
                            },
                        )
                    }
                }
                Text(
                    if (groups.isEmpty()) {
                        "Groups have not loaded from the backend yet - tap " +
                            "Reload."
                    } else {
                        "The number is how many topics that group covers. A " +
                            "greyed-out group already belongs to another " +
                            "channel."
                    },
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                if (loneTopics.isNotEmpty()) {
                    Text(
                        "Also mapped individually: " +
                            loneTopics.joinToString(", ") +
                            ". Those still work and still take precedence " +
                            "over their group.",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }
    }

    if (confirmForget) {
        AlertDialog(
            onDismissRequest = { confirmForget = false },
            title = { Text("Remove this channel?") },
            text = {
                Text(
                    "The app will forget its sign-in and stop publishing there. " +
                        "Videos already published stay up, and you can add the " +
                        "channel again at any time."
                )
            },
            confirmButton = {
                TextButton(onClick = {
                    confirmForget = false
                    onForget()
                }) { Text("Remove") }
            },
            dismissButton = {
                TextButton(onClick = { confirmForget = false }) { Text("Cancel") }
            },
        )
    }
}
