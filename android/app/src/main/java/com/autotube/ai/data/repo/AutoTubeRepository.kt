package com.autotube.ai.data.repo

import com.autotube.ai.data.local.AnalyticsEntity
import com.autotube.ai.data.local.AppDatabase
import com.autotube.ai.data.local.AutomationEntity
import com.autotube.ai.data.local.EventEntity
import com.autotube.ai.data.local.JobEntity
import com.autotube.ai.data.local.ResearchEntity
import com.autotube.ai.data.prefs.SecureStore
import com.autotube.ai.data.remote.ApiClient
import com.autotube.ai.data.remote.AutomationRequestDto
import com.autotube.ai.data.remote.AutomationListDto
import com.autotube.ai.data.remote.ClearAckDto
import com.autotube.ai.data.remote.ClearRequestDto
import com.autotube.ai.data.remote.NicheGroupListDto
import com.autotube.ai.data.remote.ScriptBankDto
import com.autotube.ai.data.remote.NicheMapBodyDto
import com.autotube.ai.data.remote.YouTubeAccountListDto
import com.autotube.ai.data.remote.CancelAckDto
import com.autotube.ai.data.remote.HealthDto
import com.autotube.ai.data.remote.JobDetailDto
import com.autotube.ai.data.remote.NichePreviewDto
import com.autotube.ai.data.remote.QuotaDto
import com.autotube.ai.data.remote.RejectBodyDto
import com.autotube.ai.data.remote.ResearchDto
import com.autotube.ai.data.remote.TokenBodyDto
import com.autotube.ai.data.remote.YouTubeStatusDto
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import retrofit2.HttpException
import java.io.IOException
import java.util.UUID

/**
 * Single entry point for data. UI observes Room; network calls refresh Room.
 *
 * Errors are returned as [Result] rather than thrown, so every screen can show
 * a real message instead of crashing when the backend is unreachable - which is
 * the normal case on mobile data.
 */
class AutoTubeRepository(
    private val db: AppDatabase,
    private val api: ApiClient,
    private val store: SecureStore,
) {

    // ---- observation (offline-first) -----------------------------------
    fun observeJobs(limit: Int = 50): Flow<List<JobEntity>> =
        db.jobs().observeRecent(limit)

    fun observeJobsByStatus(status: String): Flow<List<JobEntity>> =
        db.jobs().observeByStatus(status)

    fun observeAutomations(): Flow<List<AutomationEntity>> =
        db.automations().observeAll()

    fun observeResearch(niche: String): Flow<List<ResearchEntity>> =
        db.research().observeForNiche(niche)

    fun observeAnalytics(): Flow<List<AnalyticsEntity>> = db.analytics().observeAll()

    fun observeEvents(): Flow<List<EventEntity>> = db.events().observeRecent()

    fun countByStatus(status: String): Flow<Int> = db.jobs().countByStatus(status)

    fun countCompletedToday(): Flow<Int> =
        db.jobs().countCompletedSince(System.currentTimeMillis() - 86_400_000L)

    fun totalViews(): Flow<Long?> = db.analytics().totalViews()
    fun averageRetention(): Flow<Double?> = db.analytics().averageRetention()
    fun totalSubscribers(): Flow<Long?> = db.analytics().totalSubscribers()

    // ---- network -------------------------------------------------------
    suspend fun health(): Result<HealthDto> = call { api.service().health() }

    suspend fun nichePreview(
        niche: String,
        audience: String,
        style: String,
        duration: Int,
    ): Result<NichePreviewDto> = call {
        api.service().nichePreview(niche, audience, style, duration)
    }

    suspend fun startAutomation(request: AutomationRequestDto): Result<String> = call {
        val ack = api.service().createAutomation(request)
        // Persist locally so the Scheduler screen works offline.
        db.automations().upsert(
            AutomationEntity(
                id = ack.automationId.ifBlank { UUID.randomUUID().toString() },
                niche = request.niche,
                audience = request.audience,
                language = request.language,
                videoFormat = request.videoFormat,
                durationSeconds = request.durationSeconds,
                style = request.style,
                mode = request.mode,
                frequency = request.frequency,
                days = request.days,
                uploadTime = request.uploadTime,
                timezone = request.timezone,
                madeForKids = request.madeForKids,
                createdAt = System.currentTimeMillis(),
                // Carried so a recurring run repeats what was actually
                // chosen rather than the DTO defaults.
                voiceGender = request.voiceGender,
                captionLanguage = request.captionLanguage,
                captionStyle = request.captionStyle,
                publishMode = request.publishMode,
                channelId = request.channelId,
                scriptSource = request.scriptSource,
                nicheGroup = request.nicheGroup,
            )
        )
        logEvent("AUTOMATION", "queued ${request.niche} x${request.count}")
        ack.automationId
    }

    suspend fun refreshJobs(limit: Int = 50): Result<Int> = call {
        val response = api.service().jobs(limit = limit)
        val now = System.currentTimeMillis()
        db.jobs().upsertAll(
            response.jobs.map { j ->
                JobEntity(
                    jobId = j.jobId,
                    status = j.status,
                    niche = j.niche,
                    title = j.title,
                    qualityScore = j.qualityScore,
                    qualityPassed = j.qualityPassed,
                    retentionScore = j.retentionScore,
                    duration = j.duration,
                    blockers = j.blockers,
                    youtubeVideoId = j.youtubeVideoId,
                    scheduledFor = j.scheduledFor,
                    error = j.error,
                    retryCount = j.retryCount,
                    hasVideo = j.hasVideo,
                    hasThumbnail = j.hasThumbnail,
                    // Backend timestamps are epoch SECONDS (Python time.time()).
                    updatedAt = if (j.updatedAt > 0) (j.updatedAt * 1000).toLong() else now,
                )
            }
        )
        response.jobs.size
    }

    suspend fun jobDetail(jobId: String): Result<JobDetailDto> =
        call { api.service().job(jobId) }

    suspend fun approve(jobId: String): Result<Unit> = call {
        api.service().approve(jobId)
        logEvent("APPROVAL", "approved $jobId", jobId)
        Unit
    }

    suspend fun reject(jobId: String, reason: String): Result<Unit> = call {
        api.service().reject(jobId, RejectBodyDto(reason))
        logEvent("APPROVAL", "rejected $jobId", jobId)
        Unit
    }

    /**
     * Every brand channel the backend can publish to.
     *
     * One entry per authorisation: a YouTube token is bound to a single
     * channel, chosen in Google's own chooser during consent, so posting to
     * several brand channels under one Google account means connecting each
     * one - not one token with a channel parameter.
     */
    /**
     * The `error` slug and the human message from a FastAPI error body.
     *
     * Read ONCE and returned together, because an OkHttp error body is a
     * one-shot stream: parsing it here and again in a separate "was this the
     * kids gate" helper left the second caller with an empty string.
     *
     * Shape is {"detail": {"error": "...", "message": "..."}} for the checks
     * this backend raises and {"detail": "..."} for FastAPI's own. Returns
     * nulls rather than throwing - failing to parse an error must not replace
     * the error.
     */
    private fun errorDetail(e: HttpException): Pair<String?, String?> =
        runCatching {
            val body = e.response()?.errorBody()?.string().orEmpty()
            if (body.isBlank()) return null to null
            when (val detail = Json.parseToJsonElement(body).jsonObject["detail"]) {
                is JsonPrimitive -> null to detail.content
                is JsonObject -> (
                    detail["error"]?.jsonPrimitive?.content to
                        detail["message"]?.jsonPrimitive?.content
                    )
                else -> null to null
            }
        }.getOrDefault(null to null)

    suspend fun youtubeAccounts(): Result<YouTubeAccountListDto> =
        call { api.service().youtubeAccounts() }

    /** The channel groups and their topics. The one canonical list. */
    suspend fun nicheGroups(): Result<NicheGroupListDto> =
        call { api.service().nicheGroups() }

    suspend fun scriptBank(group: String = "", language: String = "",
                           videoFormat: String = "",
                           topic: String = ""): Result<ScriptBankDto> =
        call { api.service().scriptBank(group, language, videoFormat, topic) }

    suspend fun setDefaultAccount(channelId: String): Result<Unit> =
        call { api.service().setDefaultAccount(channelId) }

    suspend fun setAccountNiches(channelId: String,
                                 niches: List<String>): Result<Unit> =
        call { api.service().setAccountNiches(channelId, NicheMapBodyDto(niches)) }

    suspend fun removeAccount(channelId: String): Result<Unit> =
        call { api.service().removeAccount(channelId) }

    /** Every automation the backend knows about, running or scheduled. */
    suspend fun automations(): Result<AutomationListDto> =
        call { api.service().automations() }

    /**
     * Clear finished jobs and free their disk.
     *
     * `olderThanDays = 0` clears everything eligible now. The backend keeps
     * anything in flight or awaiting approval whatever is asked, so this
     * cannot abandon a running render.
     */
    suspend fun clearJobs(olderThanDays: Double = 0.0): Result<ClearAckDto> =
        call {
            val ack = api.service().clearJobs(
                ClearRequestDto(olderThanDays = olderThanDays))
            // Mirror exactly what the backend cleared. A local time cutoff
            // would delete a render that is still in progress, which the
            // backend deliberately kept.
            if (ack.jobIds.isNotEmpty()) db.jobs().deleteByIds(ack.jobIds)
            logEvent("CLEANUP", "cleared ${ack.cleared} jobs, " +
                "${ack.freedMb} MB freed")
            ack
        }

    /**
     * Stop a job. Cancellation is cooperative on the backend, so this returns
     * as soon as the request is recorded, not when the job actually stops -
     * the note in the reply says so.
     */
    suspend fun cancelJob(jobId: String): Result<CancelAckDto> = call {
        val ack = api.service().cancelJob(jobId)
        logEvent("CANCEL", "cancel requested for $jobId", jobId)
        ack
    }

    suspend fun cancelAutomation(automationId: String): Result<CancelAckDto> = call {
        val ack = api.service().cancelAutomation(automationId)
        // Disabled, not deleted. A worker that fires before WorkManager
        // settles then sees "switched off" instead of "unknown", and the
        // Schedule screen can still show it as stopped rather than having it
        // vanish - which looks like the cancel lost the automation.
        db.automations().setEnabled(automationId, false)
        logEvent("CANCEL", "automation $automationId cancelled")
        ack
    }

    suspend fun research(niche: String, videoFormat: String): Result<ResearchDto> =
        call {
            val response = api.service().research(niche, videoFormat)
            val now = System.currentTimeMillis()
            db.research().upsertAll(
                response.videos.map { v ->
                    ResearchEntity(
                        videoId = v.videoId,
                        niche = niche,
                        title = v.title,
                        channelTitle = v.channelTitle,
                        views = v.views,
                        viewVelocity = v.viewVelocity,
                        engagementRate = v.engagementRate,
                        performanceRatio = v.performanceRatio,
                        isBreakout = v.isBreakout,
                        viralScore = v.viralScore,
                        ctrPotentialScore = v.ctrPotentialScore,
                        ageDays = v.ageDays,
                        thumbnailUrl = v.thumbnailUrl,
                        fetchedAt = now,
                    )
                }
            )
            response
        }

    suspend fun refreshAnalytics(collect: Boolean = false): Result<Int> = call {
        val response = api.service().analytics(collect = collect)
        val now = System.currentTimeMillis()
        db.analytics().upsertAll(
            response.videos.map { a ->
                AnalyticsEntity(
                    videoId = a.videoId,
                    views = a.views,
                    avgViewPercentage = a.avgViewPercentage,
                    ctr = a.ctr,
                    subscribersGained = a.subscribersGained,
                    likes = a.likes,
                    comments = a.comments,
                    collectedAt = now,
                )
            }
        )
        response.videos.size
    }

    suspend fun quota(): Result<QuotaDto> = call { api.service().quota() }

    suspend fun youtubeStatus(): Result<YouTubeStatusDto> =
        call { api.service().youtubeStatus() }

    /**
     * Hand the OAuth refresh token to the backend so it can upload.
     * The token is stored encrypted on device and never logged.
     *
     * The client id goes with it. A token minted by an Android OAuth client
     * can only be refreshed by that same client, with no secret, because an
     * Android client is a public PKCE client. Sending the token alone left the
     * backend refreshing it with the desktop credentials from .env, which
     * Google rejects - so connecting from the phone looked like it worked and
     * then never uploaded anything.
     */
    suspend fun sendRefreshToken(token: String): Result<Boolean> = call {
        store.refreshToken = token
        api.service().sendRefreshToken(
            TokenBodyDto(token, store.oauthClientId.trim())
        ).stored
    }

    fun mediaUrl(path: String): String = api.mediaUrl(path)
    fun apiKeyHeader(): Pair<String, String>? = api.apiKeyHeader

    // ---- housekeeping --------------------------------------------------
    suspend fun logEvent(tag: String, message: String, jobId: String = "") {
        withContext(Dispatchers.IO) {
            db.events().add(
                EventEntity(tag = tag, message = message, jobId = jobId,
                    at = System.currentTimeMillis())
            )
        }
    }

    suspend fun prune(retainDays: Int = 30) = withContext(Dispatchers.IO) {
        val cutoff = System.currentTimeMillis() - retainDays * 86_400_000L
        db.jobs().pruneOlderThan(cutoff)
        db.events().pruneOlderThan(cutoff)
    }

    /**
     * Wraps a network call so callers get a readable failure instead of an
     * exception type. Distinguishing these matters for the UI: an auth error
     * needs a Settings prompt, a connection error just needs a retry.
     */
    private suspend fun <T> call(block: suspend () -> T): Result<T> =
        withContext(Dispatchers.IO) {
            // Fail immediately when no backend is configured.
            //
            // Otherwise the client falls back to the build's default address,
            // which is the emulator's host loopback (10.0.2.2) and unroutable
            // from a real phone. The user waited twenty seconds for a
            // connect timeout and got "cannot reach the backend" - accurate,
            // useless, and pointing at an address they never typed.
            if (store.backendUrl.isBlank()) {
                return@withContext Result.failure(
                    RepositoryException(
                        "No backend URL set yet. Open Settings and enter the " +
                            "backend URL and API key."
                    )
                )
            }
            try {
                Result.success(block())
            } catch (e: HttpException) {
                // The backend's own words first.
                //
                // Every code below had a hard-coded sentence, and 409 said
                // "see the message on screen" while nothing was on screen:
                // the dialog that would have carried it was already dismissed.
                // FastAPI puts the useful text in detail.message, so read it.
                val (slug, detail) = errorDetail(e)
                val message = detail?.takeIf { it.isNotBlank() } ?: when (e.code()) {
                    401 -> "Backend rejected the API key. Check Settings."
                    409 -> "The backend needs something confirmed first."
                    429 -> "Backend rate limit reached. Try again shortly."
                    503 -> "Backend is not ready (missing ffmpeg or API keys)."
                    else -> "Backend error ${e.code()}."
                }
                Result.failure(RepositoryException(message, e, slug))
            } catch (e: IOException) {
                Result.failure(
                    RepositoryException(
                        // Include the cause. "Cannot reach the backend" alone
                        // is indistinguishable between no network, DNS
                        // failure, a rejected certificate and an empty URL -
                        // and those need completely different fixes.
                        "Cannot reach ${store.backendUrl.ifBlank { "(no URL set)" }} " +
                            "- ${e.javaClass.simpleName}" +
                            (e.message?.take(80)?.let { ": $it" } ?: ""),
                        e,
                    )
                )
            } catch (e: Exception) {
                Result.failure(RepositoryException(e.message ?: "Unexpected error", e))
            }
        }
}

/**
 * @param errorSlug the backend's machine-readable `detail.error`, when it sent
 *   one. Carried here because the HTTP error body it came from can only be
 *   read once, and a caller that needs to branch on WHICH check failed - the
 *   child-directed gate, say - cannot go back for it.
 */
class RepositoryException(
    message: String,
    cause: Throwable? = null,
    val errorSlug: String? = null,
) : Exception(message, cause)

/** True when a failure is the child-directed confirmation gate. */
fun Throwable.isKidsConfirmation(): Boolean =
    (this as? RepositoryException)?.errorSlug == "kids_confirmation_required"
