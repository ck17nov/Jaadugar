package com.autotube.ai.data.remote

import retrofit2.http.Body
import retrofit2.http.DELETE
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.Path
import retrofit2.http.Query

/** Backend contract. Mirrors backend/api/main.py exactly. */
interface ApiService {

    @GET("health")
    suspend fun health(): HealthDto

    @GET("niche/preview")
    suspend fun nichePreview(
        @Query("niche") niche: String,
        @Query("audience") audience: String = "18-35",
        @Query("style") style: String = "",
        @Query("duration") duration: Int = 45,
        // The kids question and the caption style both depend on these: the
        // group decides child-directedness for a CUSTOM topic, and the
        // format plus the style decide which template - and one template
        // burns no captions in at all.
        @Query("group") group: String = "",
        @Query("language") language: String = "en",
        @Query("video_format") videoFormat: String = "SHORT",
    ): NichePreviewDto

    @GET("youtube/accounts")
    suspend fun youtubeAccounts(): YouTubeAccountListDto

    @POST("youtube/accounts/{channelId}/default")
    suspend fun setDefaultAccount(@Path("channelId") channelId: String): Unit

    @GET("niche-groups")
    suspend fun nicheGroups(): NicheGroupListDto

    @GET("script-bank")
    suspend fun scriptBank(
        @Query("group") group: String = "",
        @Query("language") language: String = "",
        @Query("video_format") videoFormat: String = "",
        @Query("topic") topic: String = "",
    ): ScriptBankDto

    @POST("youtube/accounts/{channelId}/niches")
    suspend fun setAccountNiches(
        @Path("channelId") channelId: String,
        @Body body: NicheMapBodyDto,
    ): Unit

    @DELETE("youtube/accounts/{channelId}")
    suspend fun removeAccount(@Path("channelId") channelId: String): Unit

    @GET("automations")
    suspend fun automations(
        @Query("include_cancelled") includeCancelled: Boolean = false,
    ): AutomationListDto

    @POST("jobs/clear")
    suspend fun clearJobs(@Body body: ClearRequestDto): ClearAckDto

    @POST("jobs/{jobId}/cancel")
    suspend fun cancelJob(@Path("jobId") jobId: String): CancelAckDto

    @DELETE("automations/{automationId}")
    suspend fun cancelAutomation(
        @Path("automationId") automationId: String,
    ): CancelAckDto

    @POST("automations")
    suspend fun createAutomation(@Body body: AutomationRequestDto): AutomationAcceptedDto

    @GET("jobs")
    suspend fun jobs(
        @Query("status") status: String = "",
        @Query("limit") limit: Int = 30,
    ): JobListDto

    @GET("jobs/{jobId}")
    suspend fun job(@Path("jobId") jobId: String): JobDetailDto

    @POST("jobs/{jobId}/approve")
    suspend fun approve(@Path("jobId") jobId: String): SimpleAckDto

    @POST("jobs/{jobId}/reject")
    suspend fun reject(
        @Path("jobId") jobId: String,
        @Body body: RejectBodyDto,
    ): SimpleAckDto

    @GET("research")
    suspend fun research(
        @Query("niche") niche: String,
        @Query("video_format") videoFormat: String = "SHORT",
        @Query("limit") limit: Int = 20,
    ): ResearchDto

    @GET("analytics")
    suspend fun analytics(
        @Query("days") days: Int = 28,
        @Query("collect") collect: Boolean = false,
    ): AnalyticsDto

    @GET("quota")
    suspend fun quota(): QuotaDto

    @GET("youtube/status")
    suspend fun youtubeStatus(): YouTubeStatusDto

    @POST("youtube/token")
    suspend fun sendRefreshToken(@Body body: TokenBodyDto): SimpleAckDto
}
