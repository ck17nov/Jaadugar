package com.autotube.ai.data.remote

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement

/**
 * Wire models for the AutoTube backend.
 *
 * Every field has a default so a backend that grows a new field, or omits an
 * optional one, never crashes the app. `ignoreUnknownKeys` is also enabled on
 * the Json instance in [ApiClient].
 */

@Serializable
data class HealthDto(
    val ok: Boolean = false,
    val version: String = "",
    val ffmpeg: Boolean = false,
    @SerialName("dry_run") val dryRun: Boolean = true,
    @SerialName("force_private") val forcePrivate: Boolean = false,
    @SerialName("upload_enabled") val uploadEnabled: Boolean = false,
    @SerialName("approval_required") val approvalRequired: Boolean = true,
    @SerialName("llm_providers") val llmProviders: List<String> = emptyList(),
    @SerialName("tts_providers") val ttsProviders: List<String> = emptyList(),
    @SerialName("research_configured") val researchConfigured: Boolean = false,
    @SerialName("auth_required") val authRequired: Boolean = false,
    @SerialName("queue_depth") val queueDepth: Int = 0,
)

@Serializable
data class AutomationRequestDto(
    val niche: String,
    val audience: String = "18-35",
    val language: String = "en",
    @SerialName("video_format") val videoFormat: String = "SHORT",
    @SerialName("duration_seconds") val durationSeconds: Int = 45,
    val style: String = "fast-paced, curiosity-driven",
    @SerialName("voice_gender") val voiceGender: String = "female",
    @SerialName("caption_language") val captionLanguage: String = "",
    @SerialName("caption_style") val captionStyle: String = "",
    @SerialName("channel_id") val channelId: String = "",
    val count: Int = 1,
    val mode: String = "APPROVAL",
    val frequency: String = "once",
    val days: List<Int> = emptyList(),
    @SerialName("upload_time") val uploadTime: String = "",
    val timezone: String = "Asia/Kolkata",
    @SerialName("made_for_kids") val madeForKids: Boolean = false,
    val keywords: List<String> = emptyList(),
    @SerialName("publish_mode") val publishMode: String = "scheduled",
    // "live" | "bank_first" | "bank". Defaults to live, so an automation
    // created by anything that has not been updated behaves exactly as it did
    // before the script bank existed.
    @SerialName("script_source") val scriptSource: String = "live",
    // The group chosen on the Create screen. Sent explicitly because a CUSTOM
    // topic cannot be matched back to a group by name, and without it the
    // video publishes to the default channel.
    @SerialName("niche_group") val nicheGroup: String = "",
    // The Settings threshold. The backend and the engine have honoured a
    // per-automation minimum all along; the app just never sent one, so the
    // "Minimum quality score to publish" slider was written to device
    // preferences and read by nothing - a video scoring 84 published with the
    // slider at 95, and one scoring 72 stayed blocked with it at 50.
    // 0 means "use the backend's configured minimum".
    @SerialName("min_quality_score") val minQualityScore: Int = 0,
    // Which automation this run belongs to. Blank = a new one, which is what
    // the Create screen sends. A RECURRING run sends the id it already has,
    // so a daily automation stays ONE automation instead of becoming a new
    // one every day - which is what made the Schedule tab list a row per run
    // and the Made-for-Kids confirmation reappear on every single video,
    // so a kids automation set to publish automatically never did.
    val id: String = "",
)

@Serializable
data class AutomationAcceptedDto(
    val accepted: Boolean = false,
    @SerialName("automation_id") val automationId: String = "",
    val queued: Int = 0,
    val note: String = "",
)

@Serializable
data class JobSummaryDto(
    @SerialName("job_id") val jobId: String = "",
    val status: String = "",
    @SerialName("created_at") val createdAt: Double = 0.0,
    @SerialName("updated_at") val updatedAt: Double = 0.0,
    val niche: String = "",
    val title: String = "",
    @SerialName("quality_score") val qualityScore: Double = 0.0,
    @SerialName("quality_passed") val qualityPassed: Boolean = false,
    val blockers: List<String> = emptyList(),
    @SerialName("retention_score") val retentionScore: Double = 0.0,
    val duration: Double = 0.0,
    @SerialName("youtube_video_id") val youtubeVideoId: String = "",
    @SerialName("scheduled_for") val scheduledFor: String = "",
    val error: String = "",
    @SerialName("retry_count") val retryCount: Int = 0,
    @SerialName("has_video") val hasVideo: Boolean = false,
    @SerialName("has_thumbnail") val hasThumbnail: Boolean = false,
)

@Serializable
data class JobListDto(
    @SerialName("queue_depth") val queueDepth: Int = 0,
    val jobs: List<JobSummaryDto> = emptyList(),
)

@Serializable
data class MediaLinksDto(
    val video: String? = null,
    val thumbnail: String? = null,
    val subtitle: String? = null,
    val voice: String? = null,
)

@Serializable
data class JobDetailDto(
    @SerialName("job_id") val jobId: String = "",
    val status: String = "",
    val error: String = "",
    @SerialName("retry_count") val retryCount: Int = 0,
    val request: JsonElement? = null,
    val idea: JsonElement? = null,
    val script: JsonElement? = null,
    val metadata: JsonElement? = null,
    val quality: JsonElement? = null,
    val assets: JsonElement? = null,
    val logs: List<String> = emptyList(),
    val media: MediaLinksDto = MediaLinksDto(),
    @SerialName("youtube_video_id") val youtubeVideoId: String = "",
    @SerialName("scheduled_for") val scheduledFor: String = "",
)

// ---- Research -----------------------------------------------------------
@Serializable
data class ResearchVideoDto(
    @SerialName("video_id") val videoId: String = "",
    val title: String = "",
    @SerialName("channel_title") val channelTitle: String = "",
    val views: Long = 0,
    val likes: Long = 0,
    val comments: Long = 0,
    @SerialName("age_days") val ageDays: Double = 0.0,
    @SerialName("view_velocity") val viewVelocity: Double = 0.0,
    @SerialName("engagement_rate") val engagementRate: Double = 0.0,
    @SerialName("performance_ratio") val performanceRatio: Double = 0.0,
    @SerialName("is_breakout") val isBreakout: Boolean = false,
    @SerialName("viral_score") val viralScore: Double = 0.0,
    /** Heuristic over public signals. NOT another channel's real CTR. */
    @SerialName("ctr_potential_score") val ctrPotentialScore: Double = 0.0,
    @SerialName("thumbnail_url") val thumbnailUrl: String = "",
    @SerialName("is_short") val isShort: Boolean = false,
)

@Serializable
data class TopicClusterDto(
    val topic: String = "",
    val keywords: List<String> = emptyList(),
    @SerialName("video_ids") val videoIds: List<String> = emptyList(),
    val momentum: Double = 0.0,
    @SerialName("breakout_count") val breakoutCount: Int = 0,
    @SerialName("title_patterns") val titlePatterns: List<String> = emptyList(),
    @SerialName("example_titles") val exampleTitles: List<String> = emptyList(),
)

@Serializable
data class ContentGapDto(
    val topic: String = "",
    @SerialName("common_angles") val commonAngles: List<String> = emptyList(),
    @SerialName("missing_angles") val missingAngles: List<String> = emptyList(),
    @SerialName("unanswered_questions") val unansweredQuestions: List<String> = emptyList(),
    @SerialName("audience_curiosity") val audienceCuriosity: String = "",
    @SerialName("gap_score") val gapScore: Double = 0.0,
)

@Serializable
data class ResearchDto(
    val niche: String = "",
    @SerialName("quota_used_today") val quotaUsedToday: Int = 0,
    @SerialName("quota_limit") val quotaLimit: Int = 10000,
    val videos: List<ResearchVideoDto> = emptyList(),
    val breakouts: List<String> = emptyList(),
    val clusters: List<TopicClusterDto> = emptyList(),
    val gaps: List<ContentGapDto> = emptyList(),
    val disclaimer: String = "",
)

// ---- Analytics ----------------------------------------------------------
@Serializable
data class AnalyticsRowDto(
    @SerialName("youtube_video_id") val videoId: String = "",
    val views: Long = 0,
    @SerialName("avg_view_percentage") val avgViewPercentage: Double = 0.0,
    val ctr: Double = 0.0,
    @SerialName("subscribers_gained") val subscribersGained: Long = 0,
    val likes: Long = 0,
    val comments: Long = 0,
)

@Serializable
data class StrategyInsightDto(
    val dimension: String = "",
    val value: String = "",
    val samples: Int = 0,
    val weight: Double = 1.0,
    @SerialName("avg_views") val avgViews: Double = 0.0,
    @SerialName("avg_retention") val avgRetention: Double = 0.0,
)

@Serializable
data class StrategyReportDto(
    val method: String = "",
    @SerialName("min_samples_to_apply") val minSamples: Int = 3,
    val insights: List<StrategyInsightDto> = emptyList(),
    val hints: String = "",
)

@Serializable
data class AnalyticsDto(
    val videos: List<AnalyticsRowDto> = emptyList(),
    val strategy: StrategyReportDto = StrategyReportDto(),
    val note: String = "",
)

// ---- Misc ---------------------------------------------------------------
@Serializable
data class QuotaDto(
    @SerialName("used_today") val usedToday: Int = 0,
    val limit: Int = 10000,
    @SerialName("reserved_for_uploads") val reservedForUploads: Int = 0,
    @SerialName("available_for_research") val availableForResearch: Int = 0,
    @SerialName("max_uploads_per_day") val maxUploadsPerDay: Int = 6,
    val resets: String = "",
)

@Serializable
data class NicheProfileDto(
    val name: String = "",
    val audience: String = "",
    val tone: String = "",
    @SerialName("visual_style") val visualStyle: String = "",
    val pacing: String = "",
    @SerialName("hook_style") val hookStyle: String = "",
    @SerialName("scene_seconds") val sceneSeconds: Double = 0.0,
    @SerialName("words_per_second") val wordsPerSecond: Double = 0.0,
    @SerialName("requires_fact_check") val requiresFactCheck: Boolean = false,
    @SerialName("is_sensitive") val isSensitive: Boolean = false,
    @SerialName("made_for_kids") val madeForKids: Boolean = false,
    val restrictions: List<String> = emptyList(),
    val disclaimers: List<String> = emptyList(),
)

@Serializable
data class NichePreviewDto(
    val profile: NicheProfileDto = NicheProfileDto(),
    @SerialName("kids_niche_detected") val kidsNicheDetected: Boolean = false,
    @SerialName("requires_kids_confirmation") val requiresKidsConfirmation: Boolean = false,
    @SerialName("child_directed_group") val childDirectedGroup: Boolean = false,
    @SerialName("style_template") val styleTemplate: String = "",
    /** "" until the backend answers. "none" means no burnt-in captions. */
    @SerialName("caption_style") val captionStyle: String = "",
    /** The language captions will be in, or "" when there will be none. */
    @SerialName("caption_language") val captionLanguage: String = "",
)

@Serializable
data class YouTubeChannelDto(
    @SerialName("channel_id") val channelId: String = "",
    val title: String = "",
    @SerialName("custom_url") val customUrl: String = "",
    val thumbnail: String = "",
    val subscribers: Long = 0,
    val videos: Long = 0,
    val views: Long = 0,
)

@Serializable
data class YouTubeStatusDto(
    val configured: Boolean = false,
    val authorized: Boolean = false,
    /** "device" (from the phone), "env" (desktop client) or "none". */
    @SerialName("client_source") val clientSource: String = "none",
    val channels: List<YouTubeChannelDto> = emptyList(),
    val error: String = "",
)

@Serializable
data class TokenBodyDto(
    @SerialName("refresh_token") val refreshToken: String,
    // The Android OAuth client that minted this token. The backend can
    // only refresh it with this same client id and no secret, because an
    // Android client is a public PKCE client.
    @SerialName("client_id") val clientId: String = "",
)

@Serializable
data class RejectBodyDto(val reason: String = "")

@Serializable
data class SimpleAckDto(
    val accepted: Boolean = false,
    val stored: Boolean = false,
    val authorized: Boolean = false,
    @SerialName("job_id") val jobId: String = "",
    val status: String = "",
)

@Serializable
data class CancelAckDto(
    val cancelled: Boolean = false,
    val status: String = "",
    @SerialName("dropped_from_queue") val droppedFromQueue: Int = 0,
    val note: String = "",
)

@Serializable
data class AutomationSummaryDto(
    val id: String = "",
    val niche: String = "",
    val frequency: String = "once",
    @SerialName("upload_time") val uploadTime: String = "",
    val days: List<Int> = emptyList(),
    val timezone: String = "",
    val enabled: Boolean = true,
    @SerialName("created_at") val createdAt: Double = 0.0,
    @SerialName("video_format") val videoFormat: String = "",
    val language: String = "",
    @SerialName("made_for_kids") val madeForKids: Boolean = false,
    @SerialName("videos_made") val videosMade: Int = 0,
    val running: Boolean = false,
    // A "just once" automation whose video has published is finished. The
    // backend hides those by default; these fields exist so the app can say
    // "1 of 1 done" rather than showing a Stop button that stops nothing.
    @SerialName("runs_finished") val runsFinished: Int = 0,
    @SerialName("runs_requested") val runsRequested: Int = 1,
    val completed: Boolean = false,
    // Where this automation publishes, resolved by the backend the same way
    // the pipeline resolves it. Without these, two daily automations are
    // indistinguishable in the list - which defeats having several.
    @SerialName("channel_id") val channelId: String = "",
    @SerialName("channel_title") val channelTitle: String = "",
    @SerialName("channel_is_default") val channelIsDefault: Boolean = false,
    val group: String = "",
    @SerialName("group_label") val groupLabel: String = "",
    // EVERYTHING NEEDED TO REBUILD THE REQUEST, so the local Room row can be
    // restored from the backend. Room's migration is destructive, so without
    // these an upgrade left every recurring automation with no row - and
    // AutomationWorker cancels its own schedule when the row is missing.
    val audience: String = "",
    val style: String = "",
    @SerialName("duration_seconds") val durationSeconds: Int = 0,
    @SerialName("voice_gender") val voiceGender: String = "",
    @SerialName("caption_language") val captionLanguage: String = "",
    @SerialName("caption_style") val captionStyle: String = "",
    @SerialName("publish_mode") val publishMode: String = "scheduled",
    @SerialName("script_source") val scriptSource: String = "live",
    @SerialName("niche_group") val nicheGroup: String = "",
    val mode: String = "",
    @SerialName("min_quality_score") val minQualityScore: Int = 0,
)

@Serializable
data class AutomationListDto(
    val automations: List<AutomationSummaryDto> = emptyList(),
    @SerialName("queue_depth") val queueDepth: Int = 0,
    val running: String = "",
)

@Serializable
data class ClearRequestDto(
    @SerialName("job_ids") val jobIds: List<String> = emptyList(),
    @SerialName("older_than_days") val olderThanDays: Double = 0.0,
    @SerialName("free_disk") val freeDisk: Boolean = true,
)

@Serializable
data class ClearAckDto(
    val cleared: Int = 0,
    @SerialName("freed_mb") val freedMb: Double = 0.0,
    @SerialName("job_ids") val jobIds: List<String> = emptyList(),
)

@Serializable
data class YouTubeAccountDto(
    @SerialName("channel_id") val channelId: String = "",
    val title: String = "",
    @SerialName("added_at") val addedAt: Double = 0.0,
    val niches: List<String> = emptyList(),
    @SerialName("is_default") val isDefault: Boolean = false,
)

@Serializable
data class YouTubeAccountListDto(
    val accounts: List<YouTubeAccountDto> = emptyList(),
    val default: String = "",
)

@Serializable
data class NicheMapBodyDto(val niches: List<String> = emptyList())

/**
 * One brand channel's subject area: the unit of channel mapping.
 *
 * Served by the backend rather than hard-coded here. Two hand-kept copies of
 * this list drift and the drift is silent - a topic missing from the app
 * simply cannot be selected, and a topic missing from the backend maps to no
 * channel at all.
 */
@Serializable
data class NicheGroupDto(
    val key: String = "",
    val label: String = "",
    @SerialName("suggested_channel") val suggestedChannel: String = "",
    val topics: List<String> = emptyList(),
    @SerialName("child_directed") val childDirected: Boolean = false,
)

@Serializable
data class NicheGroupListDto(val groups: List<NicheGroupDto> = emptyList())

/**
 * One group/language/format slot of the script bank.
 *
 * `ready` is the number that matters: entries that are unused AND have a
 * human reviewer, which are the only ones a render will claim. `unused`
 * counts everything stored, so showing that instead would promise scripts
 * that cannot actually be used.
 */
@Serializable
data class BankSlotDto(
    val group: String = "",
    val language: String = "",
    @SerialName("video_format") val videoFormat: String = "",
    val total: Int = 0,
    val unused: Int = 0,
    val ready: Int = 0,
    @SerialName("human_reviewed") val humanReviewed: Int = 0,
)

/**
 * The backend's answer for ONE automation, computed by the same code that
 * does the claiming.
 *
 * The app used to fold language dialects and sum the matching slots itself,
 * which disagreed with the backend twice: it ignored the topic filter a claim
 * applies, and it treated "en-IN" as "en" while the claim matched exactly -
 * so the screen said fifteen scripts were ready and the automation failed
 * with a full bank.
 */
@Serializable
data class BankQueryDto(
    val ready: Int = 0,
    @SerialName("human_reviewed") val humanReviewed: Int = 0,
    val unused: Int = 0,
    // Which group the count was actually taken over. A blank group is
    // resolved from the topic by the backend, the same way the claim
    // resolves it, so this can differ from what was asked for - and saying
    // "3 ready in Technical" beats implying every group was searched.
    @SerialName("resolved_group") val resolvedGroup: String = "",
)

@Serializable
data class ScriptBankDto(
    val slots: List<BankSlotDto> = emptyList(),
    @SerialName("ready_total") val readyTotal: Int = 0,
    val sources: List<String> = emptyList(),
    val query: BankQueryDto? = null,
)
