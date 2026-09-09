package com.autotube.ai.ui.vm

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import androidx.lifecycle.viewmodel.CreationExtras
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.compose.runtime.Composable
import androidx.compose.ui.platform.LocalContext
import com.autotube.ai.AutoTubeApp
import com.autotube.ai.data.local.JobEntity
import com.autotube.ai.data.prefs.SecureStore
import com.autotube.ai.data.remote.AutomationRequestDto
import com.autotube.ai.data.remote.HealthDto
import com.autotube.ai.data.remote.JobDetailDto
import com.autotube.ai.data.remote.NicheGroupDto
import com.autotube.ai.data.remote.NicheGroupListDto
import com.autotube.ai.data.remote.NichePreviewDto
import com.autotube.ai.data.remote.QuotaDto
import com.autotube.ai.data.remote.YouTubeAccountDto
import com.autotube.ai.data.remote.YouTubeAccountListDto
import com.autotube.ai.data.remote.YouTubeStatusDto
import com.autotube.ai.data.repo.AutoTubeRepository
import com.autotube.ai.data.repo.isKidsConfirmation
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

/**
 * ViewModels.
 *
 * Room Flows drive the UI so every screen renders from cache instantly and
 * offline; network refreshes are explicit and always report their error text
 * rather than failing silently.
 */

// --------------------------------------------------------------------------
@Composable
inline fun <reified T : ViewModel> appViewModel(): T {
    val app = LocalContext.current.applicationContext as AutoTubeApp
    return viewModel(factory = AppViewModelFactory(app))
}

class AppViewModelFactory(private val app: AutoTubeApp) : ViewModelProvider.Factory {
    @Suppress("UNCHECKED_CAST")
    override fun <T : ViewModel> create(modelClass: Class<T>, extras: CreationExtras): T {
        val repo = app.repository
        val store = app.secureStore
        return when {
            modelClass.isAssignableFrom(DashboardViewModel::class.java) ->
                DashboardViewModel(repo, store, app) as T
            modelClass.isAssignableFrom(CreateViewModel::class.java) ->
                CreateViewModel(repo, store, app) as T
            modelClass.isAssignableFrom(ScheduleViewModel::class.java) ->
                ScheduleViewModel(repo, store, app) as T
            modelClass.isAssignableFrom(JobViewModel::class.java) ->
                JobViewModel(repo, store) as T
            modelClass.isAssignableFrom(SettingsViewModel::class.java) ->
                SettingsViewModel(repo, store, app) as T
            else -> throw IllegalArgumentException("Unknown ViewModel $modelClass")
        }
    }
}

// --------------------------------------------------------------------------
data class UiMessage(val text: String, val isError: Boolean = false)

open class BaseViewModel : ViewModel() {
    private val _message = MutableStateFlow<UiMessage?>(null)
    val message: StateFlow<UiMessage?> = _message.asStateFlow()

    private val _busy = MutableStateFlow(false)
    val busy: StateFlow<Boolean> = _busy.asStateFlow()

    protected fun info(text: String) { _message.value = UiMessage(text, false) }
    protected fun error(text: String) { _message.value = UiMessage(text, true) }
    fun clearMessage() { _message.value = null }

    protected fun <T> runTask(
        onSuccess: (T) -> Unit = {},
        // Runs BEFORE the error banner is set, so a caller can react to WHICH
        // failure happened rather than only to the sentence describing it.
        onFailure: (Throwable) -> Unit = {},
        block: suspend () -> Result<T>,
    ) {
        viewModelScope.launch {
            _busy.value = true
            val result = block()
            _busy.value = false
            result.fold(
                onSuccess = {
                    // Clear a stale error before running the success handler.
                    //
                    // Without this, the first failure stuck to the screen for
                    // the rest of the session. A fresh install times out
                    // against the default backend address before the user has
                    // set a URL, so the Create tab kept showing "cannot reach
                    // the backend" over a niche profile it had just fetched
                    // successfully - while the Dashboard, whose own call had
                    // succeeded, showed connected. The banner was the bug; the
                    // backend was fine.
                    if (_message.value?.isError == true) _message.value = null
                    onSuccess(it)
                },
                onFailure = {
                    onFailure(it)
                    error(it.message ?: "Something went wrong")
                },
            )
        }
    }
}

// --------------------------------------------------------------------------
class DashboardViewModel(
    private val repo: AutoTubeRepository,
    val store: SecureStore,
    private val app: AutoTubeApp,
) : BaseViewModel() {

    val jobs: StateFlow<List<JobEntity>> = repo.observeJobs()
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), emptyList())

    val awaitingApproval: StateFlow<List<JobEntity>> =
        repo.observeJobsByStatus("AWAITING_APPROVAL")
            .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), emptyList())

    val publishedCount: StateFlow<Int> = repo.countByStatus("PUBLISHED")
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), 0)

    val scheduledCount: StateFlow<Int> = repo.countByStatus("SCHEDULED")
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), 0)

    val failedCount: StateFlow<Int> = repo.countByStatus("FAILED")
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), 0)

    val todayCount: StateFlow<Int> = repo.countCompletedToday()
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), 0)

    val totalViews: StateFlow<Long?> = repo.totalViews()
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), null)

    private val _health = MutableStateFlow<HealthDto?>(null)
    val health: StateFlow<HealthDto?> = _health.asStateFlow()

    private val _quota = MutableStateFlow<QuotaDto?>(null)
    val quota: StateFlow<QuotaDto?> = _quota.asStateFlow()

    val isConfigured: Boolean get() = store.isConfigured

    fun refresh() {
        if (!store.isConfigured) {
            error("Set the backend URL and API key in Settings first.")
            return
        }
        runTask<Int>({ }) { repo.refreshJobs() }
        viewModelScope.launch {
            repo.health().onSuccess { _health.value = it }
            repo.quota().onSuccess { _quota.value = it }
        }
    }

    /**
     * Stop a job that is queued or rendering.
     *
     * The backend cancels cooperatively, so the message says "stopping"
     * rather than "stopped": an encode in progress finishes before the job
     * gives up, and claiming otherwise would be a lie the user can see
     * through by watching the dashboard.
     */
    fun cancelJob(jobId: String) = runTask<com.autotube.ai.data.remote.CancelAckDto>({
        info(if (it.cancelled) "Stopping - it will halt at the next stage."
             else "Already finished; nothing to stop.")
        refresh()
    }) { repo.cancelJob(jobId) }

    /**
     * Stop a recurring automation for good.
     *
     * Three things, in this order, because each covers a different failure:
     *  1. the backend, so a queued or in-progress run stops and the
     *     cancellation is persisted (its in-memory set does not survive a
     *     restart);
     *  2. the Room row, so a worker that fires before WorkManager settles
     *     sees "disabled" rather than "unknown";
     *  3. WorkManager, which is the thing that actually makes it recur. Without
     *     this the phone still produces tomorrow's video - the whole reason
     *     stopping appeared not to work.
     */
    fun stopAutomation(automationId: String) =
        runTask<com.autotube.ai.data.remote.CancelAckDto>({
            info("Automation stopped. No further videos will be created.")
            refresh()
        }) {
            repo.cancelAutomation(automationId).also { outcome ->
                // Only tear the schedule down if the backend agreed. Killing
                // the local schedule after a failed call would leave an
                // automation the server still believes in and the phone no
                // longer runs.
                if (outcome.isSuccess) {
                    com.autotube.ai.workers.WorkScheduler.cancelAutomation(
                        app, automationId)
                }
            }
        }

    /**
     * Clear finished jobs and free the disk they were using.
     *
     * `olderThanDays = 0` clears everything eligible now. The backend decides
     * what is eligible and keeps anything in flight or awaiting approval, so
     * this cannot abandon work in progress.
     */
    fun clearJobs(olderThanDays: Double) =
        runTask<com.autotube.ai.data.remote.ClearAckDto>({
            info(
                if (it.cleared == 0) "Nothing to clear."
                else "Cleared ${it.cleared} jobs and freed ${it.freedMb} MB."
            )
            refresh()
        }) { repo.clearJobs(olderThanDays) }

    fun approve(jobId: String) = runTask<Unit>({
        // Do not promise an upload the backend cannot perform.
        //
        // In dry-run mode the backend renders everything and uploads nothing,
        // so "Uploading or scheduling now" was simply false - the video never
        // appeared in Schedule because no publishAt was ever set, and there
        // was nothing on screen to explain why.
        if (_health.value?.dryRun != false) {
            info(
                "Approved. The backend is in DRY RUN, so the video is rendered " +
                    "but not uploaded and will not appear in Schedule. Set " +
                    "dry_run: false on the backend to publish."
            )
        } else {
            info("Approved. Uploading or scheduling now.")
        }
        refresh()
    }) { repo.approve(jobId) }

    fun reject(jobId: String, reason: String = "rejected from dashboard") =
        runTask<Unit>({ info("Rejected."); refresh() }) { repo.reject(jobId, reason) }
}

// --------------------------------------------------------------------------
class CreateViewModel(
    private val repo: AutoTubeRepository,
    val store: SecureStore,
    private val app: AutoTubeApp,
) : BaseViewModel() {

    private val _preview = MutableStateFlow<NichePreviewDto?>(null)
    val preview: StateFlow<NichePreviewDto?> = _preview.asStateFlow()

    private val _started = MutableStateFlow(false)
    val started: StateFlow<Boolean> = _started.asStateFlow()

    /** Shown when the backend says this niche looks child-directed. */
    private val _kidsPrompt = MutableStateFlow(false)
    val kidsPrompt: StateFlow<Boolean> = _kidsPrompt.asStateFlow()

    /**
     * Whether the backend pins every upload to private.
     *
     * Read here so the publish selector can say that scheduling will not take
     * effect, rather than offering a choice the backend quietly ignores.
     */
    private val _forcePrivate = MutableStateFlow(false)
    val forcePrivate: StateFlow<Boolean> = _forcePrivate.asStateFlow()

    /**
     * The brand channels available to publish to, for the channel selector.
     *
     * Empty is a legitimate state - nothing connected yet - and the selector
     * hides itself rather than offering a list of nothing.
     */
    private val _channels = MutableStateFlow<List<YouTubeAccountDto>>(emptyList())
    val channels: StateFlow<List<YouTubeAccountDto>> = _channels.asStateFlow()

    init {
        if (store.isConfigured) {
            viewModelScope.launch {
                repo.health().onSuccess { _forcePrivate.value = it.forcePrivate }
            }
            loadChannels()
            loadGroups()
        }
    }

    fun loadChannels() {
        viewModelScope.launch {
            repo.youtubeAccounts().onSuccess { _channels.value = it.accounts }
        }
    }

    /**
     * The channel groups, fetched rather than hard-coded.
     *
     * Empty until the backend answers, and the screen falls back to the full
     * topic list in that case - an offline app must still be usable, and an
     * empty group dropdown that blocks the topic dropdown would be worse than
     * no grouping at all.
     */
    private val _groups = MutableStateFlow<List<NicheGroupDto>>(emptyList())
    val groups: StateFlow<List<NicheGroupDto>> = _groups.asStateFlow()

    fun loadGroups() {
        viewModelScope.launch {
            repo.nicheGroups().onSuccess { _groups.value = it.groups }
        }
    }

    /**
     * Reviewed scripts left in the bank for the current group/language/format.
     *
     * null means "not asked yet", which the screen renders as "checking"
     * rather than as zero - telling somebody there are no scripts when the
     * request simply has not returned would make them import a batch they
     * already have.
     */
    private val _bankReady = MutableStateFlow<Int?>(null)
    val bankReady: StateFlow<Int?> = _bankReady.asStateFlow()

    fun loadBank(group: String, language: String, videoFormat: String) {
        viewModelScope.launch {
            repo.scriptBank().onSuccess { bank ->
                _bankReady.value = bank.slots
                    .filter { slot ->
                        // An empty group means "any", which is what the
                        // screen shows before a group is chosen.
                        (group.isBlank() || slot.group == group) &&
                            slot.language.substringBefore("-") ==
                            language.substringBefore("-") &&
                            slot.videoFormat == videoFormat
                    }
                    .sumOf { it.ready }
            }
        }
    }

    /** Raises [kidsBlocked] when a failed start was the child-directed gate. */
    private fun noteStartFailure(error: Throwable) {
        if (error.isKidsConfirmation()) _kidsBlocked.value = true
    }

    fun previewNiche(niche: String, audience: String, style: String, duration: Int) {
        if (niche.length < 2) return
        runTask<NichePreviewDto>({
            _preview.value = it
            _kidsPrompt.value = it.requiresKidsConfirmation
        }) { repo.nichePreview(niche, audience, style, duration) }
    }

    fun dismissKidsPrompt() { _kidsPrompt.value = false }

    /**
     * The backend REFUSED the run for want of a Made-for-Kids answer.
     *
     * Different from [kidsPrompt], which is advisory - the backend thinks this
     * niche looks child-directed and would like an answer. This one is a
     * refusal, and it needs its own flag because the advisory prompt is asked
     * once per niche: answering "No, general audience" marked the niche
     * answered, START then failed with a 409 forever, and the only thing on
     * screen was "Confirmation required (see the message on screen)" with no
     * message anywhere. A dead end with no way out but reinstalling.
     */
    private val _kidsBlocked = MutableStateFlow(false)
    val kidsBlocked: StateFlow<Boolean> = _kidsBlocked.asStateFlow()

    fun dismissKidsBlocked() { _kidsBlocked.value = false }

    fun start(request: AutomationRequestDto) {
        if (!store.isConfigured) {
            error("Set the backend URL and API key in Settings first.")
            return
        }
        _kidsBlocked.value = false
        runTask<String>(onFailure = ::noteStartFailure, onSuccess = {
            _started.value = true
            info("Automation queued. Watch the Dashboard for progress.")
            com.autotube.ai.workers.WorkScheduler.syncNow(app)
            if (request.frequency != "once") {
                // The POST above already queued the FIRST run, so the
                // recurring schedule must start at the NEXT slot.
                //
                // With an initial delay of zero, WorkManager fired
                // immediately as well and every new daily automation made two
                // videos back to back - burning a day of free LLM quota on a
                // duplicate.
                val intervalHours = when (request.frequency) {
                    "weekly" -> 168L
                    // "days" included: a daily worker that returns early when
                    // today is not one of the chosen days. One periodic worker
                    // per weekday would be seven schedules to keep in step.
                    else -> 24L
                }
                com.autotube.ai.workers.WorkScheduler.scheduleAutomation(
                    app, it, intervalHours,
                    initialDelayMinutes = intervalHours * 60,
                    days = request.days,
                )
            }
        }) { repo.startAutomation(request) }
    }

    fun resetStarted() { _started.value = false }
}

// --------------------------------------------------------------------------
class ScheduleViewModel(
    private val repo: AutoTubeRepository,
    val store: SecureStore,
    private val app: AutoTubeApp,
) : BaseViewModel() {

    val jobs: StateFlow<List<JobEntity>> = repo.observeJobs(80)
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), emptyList())

    /**
     * Recurring automations, from the BACKEND.
     *
     * Room holds them too, but the server list is authoritative and survives a
     * reinstall - and until it existed there was nowhere to see what had been
     * scheduled at all.
     */
    private val _automations =
        MutableStateFlow<List<com.autotube.ai.data.remote.AutomationSummaryDto>>(
            emptyList())
    val automations: StateFlow<
        List<com.autotube.ai.data.remote.AutomationSummaryDto>> =
        _automations.asStateFlow()

    /** True once a fetch has succeeded, so "none" can be told from "unknown". */
    private val _loaded = MutableStateFlow(false)
    val loaded: StateFlow<Boolean> = _loaded.asStateFlow()

    fun refresh() {
        if (!store.isConfigured) {
            error("Set the backend URL and API key in Settings first.")
            return
        }
        viewModelScope.launch {
            // Reporting the failure matters: without it a backend that could
            // not be reached rendered as "None." - which reads as "you have no
            // automations" when the truth is "we have no idea".
            repo.automations()
                .onSuccess { _automations.value = it.automations; _loaded.value = true }
                .onFailure { error(it.message ?: "Could not load automations.") }
        }
    }

    fun stopAutomation(automationId: String) =
        runTask<com.autotube.ai.data.remote.CancelAckDto>({
            info("Automation stopped. No further videos will be created.")
            refresh()
        }) {
            repo.cancelAutomation(automationId).also { outcome ->
                if (outcome.isSuccess) {
                    com.autotube.ai.workers.WorkScheduler.cancelAutomation(
                        app, automationId)
                }
            }
        }
}

// --------------------------------------------------------------------------
class JobViewModel(
    private val repo: AutoTubeRepository,
    val store: SecureStore,
) : BaseViewModel() {

    private val _detail = MutableStateFlow<JobDetailDto?>(null)
    val detail: StateFlow<JobDetailDto?> = _detail.asStateFlow()

    val jobs: StateFlow<List<JobEntity>> = repo.observeJobs(80)
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), emptyList())

    fun load(jobId: String) {
        if (jobId.isBlank()) return
        runTask<JobDetailDto>({ _detail.value = it }) { repo.jobDetail(jobId) }
    }

    fun approve(jobId: String) = runTask<Unit>({
        info("Approved.")
        load(jobId)
    }) { repo.approve(jobId) }

    fun reject(jobId: String, reason: String) = runTask<Unit>({
        info("Rejected.")
        load(jobId)
    }) { repo.reject(jobId, reason) }

    fun mediaUrl(path: String) = repo.mediaUrl(path)
    fun apiKeyHeader() = repo.apiKeyHeader()
}

// --------------------------------------------------------------------------
class SettingsViewModel(
    private val repo: AutoTubeRepository,
    val store: SecureStore,
    private val app: AutoTubeApp,
) : BaseViewModel() {

    private val _health = MutableStateFlow<HealthDto?>(null)
    val health: StateFlow<HealthDto?> = _health.asStateFlow()

    private val _youtube = MutableStateFlow<YouTubeStatusDto?>(null)
    val youtube: StateFlow<YouTubeStatusDto?> = _youtube.asStateFlow()

    /**
     * The brand channels this backend can publish to - one per authorisation.
     *
     * Separate from [youtube], which reports the state of ONE token. A
     * YouTube token is bound to a single channel, so a kids channel and a
     * finance channel under the same Google account are two entries here,
     * not one entry with two names.
     */
    private val _accounts = MutableStateFlow<List<YouTubeAccountDto>>(emptyList())
    val accounts: StateFlow<List<YouTubeAccountDto>> = _accounts.asStateFlow()

    private val _defaultChannel = MutableStateFlow("")
    val defaultChannel: StateFlow<String> = _defaultChannel.asStateFlow()

    fun refreshAccounts() {
        runTask<YouTubeAccountListDto>({
            _accounts.value = it.accounts
            _defaultChannel.value = it.default
        }) { repo.youtubeAccounts() }
    }

    /** The channel groups, for the per-channel mapping chips. */
    private val _groups = MutableStateFlow<List<NicheGroupDto>>(emptyList())
    val groups: StateFlow<List<NicheGroupDto>> = _groups.asStateFlow()

    fun refreshGroups() {
        runTask<NicheGroupListDto>({ _groups.value = it.groups }) {
            repo.nicheGroups()
        }
    }

    fun makeDefault(channelId: String) {
        runTask<Unit>({
            info("Default channel set.")
            refreshAccounts()
        }) { repo.setDefaultAccount(channelId) }
    }

    /**
     * Map niches to a channel. A niche belongs to exactly one channel, so the
     * backend removes it from any other - which is why the whole list is
     * refreshed afterwards rather than just this row.
     */
    fun assignNiches(channelId: String, niches: List<String>) {
        runTask<Unit>({ refreshAccounts() }) {
            repo.setAccountNiches(channelId, niches)
        }
    }

    fun forgetChannel(channelId: String) {
        runTask<Unit>({
            info("Channel removed. Videos already published stay up.")
            refreshAccounts()
        }) { repo.removeAccount(channelId) }
    }

    fun testConnection() {
        runTask<HealthDto>({
            _health.value = it
            info("Connected to backend ${it.version}.")
        }) { repo.health() }
    }

    fun refreshYouTube() {
        runTask<YouTubeStatusDto>({ _youtube.value = it }) { repo.youtubeStatus() }
    }

    fun sendRefreshToken(token: String) {
        runTask<Boolean>({
            if (it) {
                info("YouTube connected. The backend can now upload.")
                refreshYouTube()
                // The backend files the new token under a placeholder and then
                // asks YouTube which channel it acts as, so the channel list
                // only becomes correct after that round trip.
                refreshAccounts()
            } else {
                error("Backend did not store the token.")
            }
        }) { repo.sendRefreshToken(token) }
    }

    fun reportAuthError(text: String) = error(text)

    fun clearSecrets() {
        store.clearSecrets()
        info("Stored credentials cleared from this device.")
    }
}
