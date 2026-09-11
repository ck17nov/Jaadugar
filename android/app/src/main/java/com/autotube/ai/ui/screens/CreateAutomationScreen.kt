package com.autotube.ai.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Checkbox
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Slider
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.autotube.ai.data.remote.AutomationRequestDto
import com.autotube.ai.ui.components.BannerTone
import com.autotube.ai.ui.components.InfoBanner
import com.autotube.ai.ui.components.LoadingRow
import com.autotube.ai.ui.components.SectionTitle
import com.autotube.ai.ui.vm.CreateViewModel
import com.autotube.ai.ui.vm.appViewModel
import kotlin.math.roundToInt
import androidx.compose.runtime.saveable.rememberSaveable
import com.autotube.ai.ui.components.LabeledDropdown
import kotlinx.coroutines.delay

// Common niches. "Other…" in the dropdown opens a free-text field, so this
// does not need to be exhaustive - it only needs to cover the usual cases
// without making the user type.
// The six areas this channel publishes in, and nothing else.
//
// A long tail of niches was worse than useless: it made the dropdown a
// scrolling list, and every extra option is a topic whose template, pacing and
// visual style nobody has tuned. These are grouped so related topics sit
// together in the list.
// THE OFFLINE FALLBACK ONLY. The Topic dropdown reads the live catalogue
// from /niche-groups; this is what it shows when the backend has not answered
// yet or cannot be reached.
//
// It used to be the DEFAULT source, which made it a duplicate nobody
// maintained: when AI, science and code were folded into Technical and four
// topics were added, this list kept the old shape - so the four newly added
// topics were missing from the list the user sees first. Keep it in step with
// engine/core/groups.py.
val NICHE_OPTIONS = listOf(
    // Kids
    "kids bedtime stories",
    "kids moral stories",
    "kids rhymes and poems",
    "kids alphabet learning",
    "kids numbers and counting",
    "kids words and spelling",
    "kids sentences and speaking",
    "kids toys and play",
    "kids shapes and colours",
    // Finance
    "personal finance",
    "finance news",
    // Technical - AI, science and code all live here now
    "youtube tips and growth",
    "pc and laptop tech",
    "new phone and laptop launches",
    "phone and laptop buying advice",
    "excel tips and tricks",
    "ms office tips and tricks",
    "AI explained",
    "AI news",
    "AI tools and courses",
    "science facts",
    "science experiments",
    "sql and databases",
    "programming and coding",
    "developer tools",
    "windows tips and tricks",
    "android tips and tricks",
    "iphone tips and tricks",
    "browser tips and tricks",
    "google tools and workspace",
    "hidden features and shortcuts",
    "computer troubleshooting",
    "file management and backup",
    "cybersecurity basics",
    "privacy and online safety",
    "cloud storage explained",
    "networking basics",
    "productivity software and apps",
    "tech myths busted",
    "youtube automation and monetisation",
)

// The Topic dropdown's Auto entry. A UI-ONLY value: the submit handler
// turns it into topicRotate=true and a blank niche, so it never crosses the
// wire. Kept out of NICHE_OPTIONS on purpose - that list is checked against
// the backend's topic catalogue by tests/test_app_backend_contract.py.
const val AUTO_TOPIC = "__auto_rotate__"

// Niches that are child-directed by definition. Picking one of these sets the
// Made for Kids flag without prompting: being asked to confirm on every
// keystroke, for a niche literally named "kids", is noise rather than consent.
val KIDS_NICHES = setOf(
    "kids bedtime stories", "kids moral stories", "kids rhymes and poems",
    "kids alphabet learning", "kids numbers and counting",
    "kids words and spelling", "kids sentences and speaking",
    "kids toys and play", "kids shapes and colours",
)

// Four languages, not twelve.
//
// The backend still has voices for more, but offering them here invited a
// choice nobody wanted to make. "hi-Latn" is Hinglish: Latin-script
// Hindi-English code-mixing, voiced by an Indian-English speaker, because a
// Hindi voice expects Devanagari and mispronounces romanised text.
val LANGUAGES = listOf(
    "hi" to "Hindi",
    "en-IN" to "Indian English",
    "en" to "English",
    "hi-Latn" to "Hinglish",
)

// Where the script comes from.
//
// "bank" refusing to fall back is deliberate and is the whole reason it is a
// separate option from "bank_first": somebody who chose "only my reviewed
// scripts" and silently got a freshly generated one has had the review
// guarantee removed without being told.
val SCRIPT_SOURCES = listOf(
    "live" to "Write a new one each time",
    "bank_first" to "Use my reviewed scripts, then write new ones",
    "bank" to "Only my reviewed scripts",
)

// Which language the captions come out in, for the one line of explanation
// the Create screen shows. The rule itself lives in
// engine/core/languages.py::caption_for - this only has to SAY it, and if the
// two ever disagree the backend is right.
fun captionNoteFor(language: String): String = when {
    language.startsWith("hi") -> "English (your voice is Hindi)"
    else -> "Hindi (your voice is English)"
}

val VOICES = listOf(
    "female" to "Female",
    "male" to "Male",
    "child" to "Child (English only; approximated elsewhere)",
)

val STYLES = listOf(
    "fast-paced, curiosity-driven",
    "calm and cinematic",
    "storytelling",
    "educational and clear",
    "high-energy entertainment",
    "gentle and simple (for young children)",
)

// Under-13 bands were missing entirely, which made it impossible to describe
// the audience for children's content - the one category where age actually
// changes the safety profile and the vocabulary.
val AUDIENCES = listOf(
    "2-4", "5-7", "8-12", "13-17", "18-24", "18-35", "25-44", "35+", "all ages",
)

// Under-13 bands. Selecting one makes the video child-directed under
// YouTube's rules whatever else is chosen, so the Made for Kids switch is
// locked on rather than merely defaulted. The backend enforces the same rule,
// so the two can never disagree.
val CHILD_AUDIENCES = setOf("2-4", "5-7", "8-12")

val FREQUENCIES = listOf("once", "daily", "weekly", "days")
private val WEEKDAYS = listOf("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun CreateAutomationScreen(onStarted: () -> Unit) {
    val vm: CreateViewModel = appViewModel()
    val preview by vm.preview.collectAsStateWithLifecycle()
    val forcePrivate by vm.forcePrivate.collectAsStateWithLifecycle()
    val kidsPrompt by vm.kidsPrompt.collectAsStateWithLifecycle()
    val kidsBlocked by vm.kidsBlocked.collectAsStateWithLifecycle()
    val started by vm.started.collectAsStateWithLifecycle()
    val channels by vm.channels.collectAsStateWithLifecycle()
    val groups by vm.groups.collectAsStateWithLifecycle()
    val bankReady by vm.bankReady.collectAsStateWithLifecycle()
    // Failed, as opposed to not answered yet. Rendering the two the same way
    // is what left "Checking..." on screen for ever with the backend down.
    val bankFailed by vm.bankFailed.collectAsStateWithLifecycle()
    // Whether the LIVE topic catalogue could be fetched at all.
    val catalogueFailed by vm.catalogueFailed.collectAsStateWithLifecycle()
    // Whether the CHANNEL list could be fetched, which is what makes
    // "not mapped" a true statement rather than a guess.
    val channelsFailed by vm.channelsFailed.collectAsStateWithLifecycle()
    // Which group that count was taken over - not always the one
    // asked for, because a blank group is resolved from the topic.
    val bankGroup by vm.bankGroup.collectAsStateWithLifecycle()
    val busy by vm.busy.collectAsStateWithLifecycle()
    val message by vm.message.collectAsStateWithLifecycle()

    // rememberSaveable, NOT remember.
    //
    // The bottom bar navigates with saveState/restoreState, which restores the
    // NavBackStackEntry - but plain `remember` is not part of that. Every
    // field reset to its default the moment you switched tab and came back,
    // silently discarding whatever had been filled in.
    var niche by rememberSaveable { mutableStateOf("") }
    var audience by rememberSaveable { mutableStateOf("18-35") }
    var language by rememberSaveable { mutableStateOf(LANGUAGES.first().first) }
    var isShort by rememberSaveable { mutableStateOf(true) }
    var lengthSeconds by rememberSaveable { mutableIntStateOf(45) }
    var style by rememberSaveable { mutableStateOf(STYLES.first()) }
    var frequency by rememberSaveable { mutableStateOf("daily") }
    // A List, not a Set: ArrayList is saveable out of the box, whereas a Set
    // needs a custom Saver whose types Kotlin cannot infer through the `by`
    // delegate. Order is irrelevant here - it is sorted before being sent.
    var selectedDays by rememberSaveable { mutableStateOf(listOf(0, 1, 2, 3, 4)) }
    var uploadTime by rememberSaveable { mutableStateOf("20:00") }
    var count by rememberSaveable { mutableIntStateOf(1) }
    var autoMode by rememberSaveable { mutableStateOf(vm.store.autoApprove) }
    // Whether the operator has deliberately overridden the Settings default
    // FOR THIS automation. Without it the screen cannot tell "I want
    // approval just this once" from "I have not touched it".
    var autoModeTouched by rememberSaveable { mutableStateOf(false) }
    var madeForKids by rememberSaveable { mutableStateOf(false) }
    // THE HUMAN ACT, captured separately from the flag.
    //
    // For a Kids group the app sets madeForKids itself, disables the switch
    // and suppresses the consent dialog - so the operator performs no
    // affirmative act at all and made_for_kids on the wire means "something
    // detected kids content". The backend needs the other fact, and held
    // every kids video waiting for it. Sending kidsConfirmed = madeForKids
    // would fabricate a consent nobody gave; this is ticked deliberately.
    var kidsConfirmed by rememberSaveable { mutableStateOf(false) }
    // Blank means "let the backend decide from the niche map, then the
    // default channel" - which is the behaviour people set the mapping up
    // for, so it stays the default rather than pre-selecting a channel.
    var channelId by rememberSaveable { mutableStateOf("") }
    // The channel group - the unit of channel mapping. Blank means "all
    // topics", which is what an offline app or a fresh install shows.
    var groupKey by rememberSaveable { mutableStateOf("") }
    // Remembers which niche the kids question was already answered for, so it
    // is asked once per niche rather than on every preview refresh.
    var kidsAnsweredFor by rememberSaveable { mutableStateOf("") }
    // "scheduled" keeps the previous behaviour; frequency and publish timing
    // used to be the same setting, so a daily automation could not put each
    // video up straight away.
    var publishMode by rememberSaveable { mutableStateOf("scheduled") }
    var voiceGender by rememberSaveable { mutableStateOf("female") }
    // Where the script comes from. "live" writes one now; "bank_first" prefers
    // a reviewed script from the bank and writes one when the bank is empty;
    // "bank" refuses rather than falling back, so a batch that was reviewed
    // is the only thing that can publish.
    var scriptSource by rememberSaveable { mutableStateOf("live") }

    // Made for Kids follows the NICHE and the AGE BAND, and is cleared when
    // neither applies.
    //
    // It used to be set true for a kids niche and never set back, and the
    // state is rememberSaveable - so after making a kids video, switching to
    // "personal finance" left the flag on. That turned a finance video for
    // 25-44 into child-directed content and made the backend research
    // "personal finance for kids". A one-way switch is the bug.
    //
    // The age band is authoritative because that is what YouTube's own
    // question asks: who is the video for. An under-13 audience is
    // child-directed whatever else is selected, which is why the toggle below
    // is disabled rather than merely pre-set in that case.
    // THE CHOSEN GROUP COUNTS, not only the topic string.
    //
    // KIDS_NICHES is a list of the nine LISTED kids topics, so a custom topic
    // under the Kids group - "story of the thirsty crow" - was not
    // child-directed as far as this screen was concerned. Set the age band to
    // "all ages", which is a natural thing to do on a kids channel, and the
    // effect below then cleared madeForKids: the POST said made_for_kids
    // =false, the backend's niche-string gate agreed, and a children's story
    // published to the kids channel as general-audience content with none of
    // the kids safety profile. `child_directed` arrives per group from
    // /niche-groups and every other part of the system honours it.
    val groupIsKids = groups.firstOrNull { it.key == groupKey }?.childDirected == true
    val nicheIsKids = niche.trim().lowercase() in KIDS_NICHES || groupIsKids
    val audienceIsChildren = audience in CHILD_AUDIENCES
    val kidsRequired = nicheIsKids || audienceIsChildren
    // Already confirmed for this channel group, so do not ask again. The
    // backend reports this from its own record, which is what makes the
    // confirmation genuinely once rather than once per automation.
    val kidsAlreadyConfirmed = preview?.kidsConfirmedForGroup == true
    // Auto mode for TOPICS. Named `rotating`, never `autoMode` - that one
    // already means AUTO-vs-APPROVAL publishing.
    val rotating = niche == AUTO_TOPIC

    // RE-SEED THE MODE FROM SETTINGS.
    //
    // `autoMode` is seeded once from the store, and the nav graph keeps this
    // screen's saved state across a trip to Settings - so turning on
    // "Default to AUTO mode" there did not reach an already-composed Create
    // screen, and the toggle looked like it had done nothing. Guarded on
    // autoModeTouched so a deliberate per-automation override still wins.
    LaunchedEffect(Unit) {
        if (!autoModeTouched) autoMode = vm.store.autoApprove
    }
    LaunchedEffect(nicheIsKids, audienceIsChildren) {
        if (kidsRequired) {
            madeForKids = true
            if (nicheIsKids && !audienceIsChildren) audience = "5-7"
            vm.dismissKidsPrompt()
        } else {
            // Leaving a kids niche for an adult one clears it.
            madeForKids = false
            kidsConfirmed = false
            kidsAnsweredFor = ""
        }
    }

    // Ask the backend how it will interpret the niche. DEBOUNCED so that
    // typing a custom topic fires one request instead of one per keystroke.
    //
    // (The rate limiter is NOT why this screen used to report "cannot reach
    // backend": /niche/preview answers 200 in under a second and survives a
    // 10-call burst. The transport was the problem - see RetryIdempotent in
    // ApiClient.)
    LaunchedEffect(niche, audience, style, lengthSeconds, groupKey, language,
                   isShort) {
        // NOT in Auto mode: the sentinel is not a subject, and sending it
        // would make the profile card describe a video about
        // "__auto_rotate__".
        if (niche != AUTO_TOPIC && niche.trim().length >= 3) {
            delay(600)
            vm.previewNiche(niche.trim(), audience, style, lengthSeconds,
                            groupKey, language,
                            if (isShort) "SHORT" else "LONGFORM")
        }
    }

    // Only when the bank is actually in play. Asking on every screen open
    // would cost a request that the default "write a new one each time" has
    // no use for.
    LaunchedEffect(scriptSource, groupKey, language, isShort, niche) {
        if (scriptSource != "live") {
            // The topic goes too: a claim filters on it, so a count that
            // ignores it promises scripts the render will not take.
            // In Auto mode the honest count is over the whole GROUP, since
            // every topic in it will be drawn from.
            vm.loadBank(groupKey, language,
                if (isShort) "SHORT" else "LONGFORM",
                if (niche == AUTO_TOPIC) "" else niche.trim())
        }
    }

    LaunchedEffect(started) {
        if (started) {
            vm.resetStarted()
            onStarted()
        }
    }

    Column(
        Modifier
            .fillMaxWidth()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("Create automation", style = MaterialTheme.typography.displaySmall)

        message?.let { msg ->
            InfoBanner(
                text = msg.text,
                tone = if (msg.isError) BannerTone.Error else BannerTone.Success,
                actionLabel = "Dismiss",
                onAction = { vm.clearMessage() },
            )
        }

        // ---- niche ------------------------------------------------------
        SectionTitle("Niche")

        // The GROUP comes first, and it is what picks the channel.
        //
        // Mapping individual topics to channels meant nine ticks for the kids
        // channel alone, and missing one sent that topic silently to the
        // default channel. A group is one tick, and choosing it here narrows
        // the topic list below to that group - so "Kids" then "bedtime
        // stories" replaces scrolling twenty-one topics looking for the nine
        // that start with "kids".
        val selectedGroup = groups.firstOrNull { it.key == groupKey }

        // AN EMPTY CATALOGUE IS NOT A DESIGN, IT IS A FAILURE - say which.
        //
        // The selector simply hid itself and the Topic dropdown fell back to
        // the list built into the app, so a backend that was not running
        // looked identical to "there are no groups". The built-in list is
        // also older than the backend's by definition, which is how the
        // retired AI, science and code sections went on appearing after they
        // had been merged into Technical.
        if (groups.isEmpty() && catalogueFailed) {
            Text(
                "Cannot reach the backend, so the live topic list is not " +
                    "available. The topics below are the copy built into " +
                    "this app and may be out of date - channel groups, and " +
                    "anything added recently, will not appear until the " +
                    "backend answers.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.error,
            )
            OutlinedButton(onClick = { vm.loadGroups() }) {
                Text("Retry")
            }
        }

        if (groups.isNotEmpty()) {
            LabeledDropdown(
                label = "Channel group",
                value = groupKey,
                options = listOf("") + groups.map { it.key },
                display = { key ->
                    if (key.isBlank()) "All topics" else {
                        groups.firstOrNull { it.key == key }?.let { g ->
                            val target = channels.firstOrNull { ch ->
                                ch.niches.any { it.equals(g.key, true) }
                            }
                            // Show where it actually publishes when a channel
                            // is mapped, and the intended name when not.
                            "${g.label} - ${target?.title ?: g.suggestedChannel}"
                        } ?: key
                    }
                },
                onValueChange = { picked ->
                    groupKey = picked
                    // Drop a topic that belongs to a DIFFERENT group's list,
                    // rather than leaving a mismatched pair on screen.
                    //
                    // A CUSTOM topic is kept. The group chooses the channel,
                    // not the subject, so free text belongs to whichever
                    // group the operator picked - and replacing it silently
                    // was worse than a mismatch: the typed topic was gone,
                    // the group's first topic was submitted in its place, and
                    // the screen still showed the text that had been
                    // discarded.
                    val allowed = groups.firstOrNull { it.key == picked }?.topics
                    val listedElsewhere = groups.any { g ->
                        g.key != picked && niche in g.topics
                    }
                    if (allowed != null && niche !in allowed && listedElsewhere) {
                        niche = allowed.firstOrNull() ?: niche
                    }
                },
            )
            selectedGroup?.let { g ->
                val target = channels.firstOrNull { ch ->
                    ch.niches.any { it.equals(g.key, true) }
                }
                Text(
                    when {
                        target != null -> "Publishes to ${target.title}."
                        // "Not mapped" is a claim about Settings; it needs
                        // the channel list to be true. Without it the
                        // screen sent the operator to fix a mapping that
                        // was already correct.
                        channelsFailed || channels.isEmpty() ->
                            "Cannot reach the backend, so where ${g.label} " +
                                "publishes is unknown. It will use whatever " +
                                "is mapped there."
                        else ->
                            "No channel is mapped to ${g.label} yet - it will " +
                                "use the default channel. Map it in Settings > " +
                                "Publishing channels."
                    },
                    style = MaterialTheme.typography.bodySmall,
                    color = when {
                        target != null -> MaterialTheme.colorScheme.primary
                        channelsFailed || channels.isEmpty() ->
                            MaterialTheme.colorScheme.error
                        else -> MaterialTheme.colorScheme.onSurfaceVariant
                    },
                )
            }
        }

        LabeledDropdown(
            label = "Topic",
            // Narrowed to the chosen group, or everything when none is chosen
            // or the backend has not answered yet.
            value = niche,
            // THE BACKEND'S CATALOGUE, not a hand-kept copy of it.
            //
            // NICHE_OPTIONS was a duplicate of the topic list that nobody
            // updated when the groups were merged, and it is what this
            // dropdown showed whenever no group was selected - the default
            // state. So the four topics that had just been added (Excel, MS
            // Office, phone and laptop launches, buying advice) were missing
            // from the list the user looks at first. It survives only as the
            // offline fallback.
            // Auto sits at the top, and only with a group chosen - which
            // is how "rotation needs a group" is expressed in the UI rather
            // than as a validation error. It has to be IN `options`:
            // LabeledDropdown treats an out-of-list value as custom text and
            // would open the free-text box instead.
            options = selectedGroup?.let { listOf(AUTO_TOPIC) + it.topics }
                ?: groups.flatMap { it.topics }.distinct().ifEmpty { NICHE_OPTIONS },
            display = { topic ->
                if (topic == AUTO_TOPIC) "Auto - every topic in rotation"
                else topic
            },
            allowOther = true,
            otherLabel = "Other topic…",
            // Selecting "Other topic…" used to appear to do nothing at all -
            // see LabeledDropdown for why. Now that it works, say what can go
            // in the box: a whole subject, not just a category name.
            otherPlaceholder = "e.g. how a sinking fund works for school fees",
            otherHelp = if (selectedGroup != null) {
                "Anything in ${selectedGroup.label}. Write it as a subject or " +
                    "a question - the script is written from this."
            } else {
                "Write the subject or question the video should answer."
            },
            onValueChange = { niche = it },
        )
        // What "Auto" actually does, in the numbers of the chosen group. This
        // is the whole explanation of the selection, so it says the lap
        // length, where the channel and the kids answer come from, and that
        // the cursor is not stored on the phone.
        if (rotating) {
            selectedGroup?.let { g ->
                Text(
                    "One video per run, each on the NEXT topic in " +
                        "${g.label}. ${g.topics.size} topics, so every " +
                        "topic gets a turn every ${g.topics.size} runs, and " +
                        "the lap picks up where it left off even after you " +
                        "reinstall the app. The channel and the " +
                        "Made-for-Kids setting come from the group, so they " +
                        "are the same for every topic. The length below " +
                        "applies to all of them - a banked script still " +
                        "uses its own.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.primary,
                )
            }
        } else if (groupKey.isBlank() && groups.isNotEmpty()) {
            Text(
                "Pick a channel group to get the Auto topic option - " +
                    "rotation runs inside one group, because that is what " +
                    "fixes the channel and the Made-for-Kids answer.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        if (nicheIsKids) {
            Text(
                "Child-directed niche: Made for Kids is set automatically and " +
                    "the stricter safety profile applies.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.primary,
            )
        }

        LabeledDropdown(
            label = "Script",
            value = scriptSource,
            options = SCRIPT_SOURCES.map { it.first },
            display = { key ->
                SCRIPT_SOURCES.firstOrNull { it.first == key }?.second ?: key
            },
            onValueChange = { scriptSource = it },
        )
        if (scriptSource != "live") {
            val ready = bankReady
            Text(
                when {
                    // FIRST, because a failed check also leaves `ready` null
                    // and "Checking..." then stayed on screen indefinitely.
                    bankFailed ->
                        "Cannot reach the backend, so the number of reviewed " +
                            "scripts is unknown. Start it and tap Retry - " +
                            "the automation will still run if the backend is " +
                            "up by then."
                    ready == null -> "Checking how many reviewed scripts are left…"
                    ready == 0 && scriptSource == "bank" ->
                        "No reviewed scripts left. This automation will FAIL " +
                            "rather than write one, which is the point of " +
                            "this option - import a batch first."
                    ready == 0 ->
                        "No reviewed scripts left, so each video will be " +
                            "written fresh until you import a batch."
                    // Names the group the count was taken over. The backend
                    // resolves a blank group from the topic - the same way
                    // the claim resolves it - so "3 ready" can mean "3 in
                    // Technical" rather than "3 anywhere".
                    else -> "$ready reviewed script(s) ready" +
                        (bankGroup.takeIf { it.isNotBlank() && groupKey.isBlank() }
                            ?.let { " in ${'$'}it" } ?: "") +
                        ". Each video uses " +
                        "the next one and its own length, so the duration " +
                        "above becomes a filter rather than a target."
                },
                style = MaterialTheme.typography.bodySmall,
                color = if (bankFailed ||
                    (bankReady == 0 && scriptSource == "bank")) {
                    MaterialTheme.colorScheme.error
                } else {
                    MaterialTheme.colorScheme.onSurfaceVariant
                },
            )
            if (bankFailed) {
                OutlinedButton(onClick = {
                    vm.loadBank(groupKey, language,
                        if (isShort) "SHORT" else "LONGFORM", niche.trim())
                    // The catalogue almost certainly failed for the same
                    // reason, so retry both rather than making the user find
                    // two buttons.
                    vm.loadGroups()
                }) {
                    Text("Retry")
                }
            }
        }

        // How the backend interpreted it - transparency about what will be made.
        preview?.let { p ->
            Card(
                colors = CardDefaults.cardColors(
                    containerColor = MaterialTheme.colorScheme.surfaceVariant
                ),
                shape = RoundedCornerShape(12.dp),
            ) {
                Column(Modifier.padding(12.dp)) {
                    SectionTitle("Interpreted profile")
                    Text(
                        "Tone: ${p.profile.tone}",
                        style = MaterialTheme.typography.bodySmall,
                    )
                    Text(
                        "Visuals: ${p.profile.visualStyle}",
                        style = MaterialTheme.typography.bodySmall,
                    )
                    Text(
                        "Pace: ${p.profile.pacing} - a new image about every " +
                            "${p.profile.sceneSeconds}s",
                        style = MaterialTheme.typography.bodySmall,
                    )
                    if (p.profile.requiresFactCheck) {
                        Text(
                            "Factual niche: claims will be checked and risky " +
                                "ones flagged for review.",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.primary,
                        )
                    }
                    p.profile.disclaimers.forEach {
                        Text(
                            "Disclaimer added: $it",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.primary,
                        )
                    }
                }
            }
        }

        // ---- audience + language ---------------------------------------
        LabeledDropdown(
            label = "Audience age",
            value = audience,
            options = AUDIENCES,
            onValueChange = { audience = it },
        )

        LabeledDropdown(
            label = "Narrator voice",
            value = voiceGender,
            options = VOICES.map { it.first },
            display = { key -> VOICES.firstOrNull { it.first == key }?.second ?: key },
            onValueChange = { voiceGender = it },
        )
        if (voiceGender == "child" && !language.startsWith("en")) {
            Text(
                "A real child voice exists only for English. For other " +
                    "languages this is the female voice pitched up and slowed " +
                    "slightly - child-friendly rather than an actual child.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.primary,
            )
        }

        // No caption controls. Asked for: "why is it required? caption should
        // be just what is in audio. style should also be auto chosen based on
        // niche/scripts."
        //
        // The LANGUAGE is derived - Hindi voice gets English captions and
        // English voice gets Hindi captions, decided by
        // engine/core/languages.py - and the STYLE comes from the niche
        // profile and the style template, which already know whether karaoke
        // or block reads better for this kind of video. Two fewer things to
        // get wrong per automation, and they can no longer disagree with what
        // the script bank stores.
        // SAY WHAT WILL ACTUALLY HAPPEN, which is not always "captions in
        // the other language". One template - the long-form illustrated
        // explainer - deliberately burns none in, and this line promised
        // them anyway: a long-form storytelling video shipped with no
        // captions on screen while the app had said they would be in Hindi.
        // The backend derives the style, so the backend is asked.
        val derivedCaptionStyle = preview?.captionStyle ?: ""
        Text(
            when {
                derivedCaptionStyle == "none" ->
                    "Captions: none burned in - this look reads better " +
                        "clean. A ${captionNoteFor(language).substringBefore(' ')} " +
                        "subtitle track is still uploaded with the video."
                derivedCaptionStyle.isNotBlank() ->
                    "Captions: ${captionNoteFor(language)}, " +
                        "$derivedCaptionStyle style - chosen automatically " +
                        "for this niche."
                else ->
                    "Captions: ${captionNoteFor(language)}. Style is chosen " +
                        "automatically for this niche."
            },
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )

        LabeledDropdown(
            label = "Language",
            value = language,
            options = LANGUAGES.map { it.first },
            display = { code -> LANGUAGES.firstOrNull { it.first == code }?.second ?: code },
            onValueChange = { language = it },
        )

        // ---- format + length -------------------------------------------
        SectionTitle("Format")
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            FilterChip(
                selected = isShort,
                onClick = {
                    isShort = true
                    if (lengthSeconds > 180) lengthSeconds = 45
                },
                label = { Text("Short (9:16)") },
            )
            FilterChip(
                selected = !isShort,
                onClick = {
                    isShort = false
                    if (lengthSeconds < 180) lengthSeconds = 300
                },
                label = { Text("Long-form (16:9)") },
            )
        }

        SectionTitle("Length: ${formatLength(lengthSeconds)}")
        Slider(
            value = lengthSeconds.toFloat(),
            onValueChange = { lengthSeconds = it.roundToInt() },
            // Long-form goes to the backend's own ceiling (3600s). Nothing in
            // the pipeline generates video, so length is not capped by a
            // service - only by YouTube's account limits and render time.
            valueRange = if (isShort) 15f..180f else 120f..3600f,
            steps = if (isShort) 32 else 57,
        )
        if (isShort && lengthSeconds > 60) {
            Text(
                "Shorts up to 3 minutes are supported by YouTube, but 30-60s " +
                    "usually retains best.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        if (!isShort && lengthSeconds > 900) {
            Text(
                "Over 15 minutes needs a verified YouTube account: Studio -> " +
                    "Settings -> Channel -> Feature eligibility. It is free. " +
                    "Unverified channels are refused at upload, not here.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.error,
            )
        }
        if (!isShort) {
            Text(
                "Long-form needs an LLM key on the backend (Groq or Gemini, " +
                    "both free). Roughly ${estimateRenderMinutes(lengthSeconds)} " +
                    "minutes of render time on a laptop.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }

        LabeledDropdown(
            label = "Style",
            value = style,
            options = STYLES,
            allowOther = true,
            otherLabel = "Other style…",
            onValueChange = { style = it },
        )

        // ---- which channel ----------------------------------------------
        //
        // Hidden entirely when only one channel is connected: a dropdown with
        // one entry is a question with one answer.
        if (channels.size > 1) {
            val byNiche = channels.firstOrNull {
                it.niches.any { n -> n.equals(niche.trim(), ignoreCase = true) }
            }
            LabeledDropdown(
                label = "Publish to",
                value = channelId,
                options = listOf("") + channels.map { it.channelId },
                display = { id ->
                    if (id.isBlank()) {
                        "Automatic" + (byNiche?.let { " - ${it.title}" } ?: "")
                    } else {
                        channels.firstOrNull { it.channelId == id }?.title
                            ?.ifBlank { id } ?: id
                    }
                },
                onValueChange = { channelId = it },
            )
            Text(
                if (channelId.isBlank() && byNiche != null) {
                    "This niche is mapped to ${byNiche.title}."
                } else if (channelId.isBlank()) {
                    "No channel is mapped to this niche, so the default " +
                        "channel will be used. Map niches to channels in " +
                        "Settings."
                } else {
                    "Overrides the niche mapping for this automation."
                },
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }

        // ---- schedule ---------------------------------------------------
        LabeledDropdown(
            label = "Upload frequency",
            value = frequency,
            options = FREQUENCIES,
            display = {
                when (it) {
                    "once" -> "Just once"
                    "daily" -> "Daily"
                    "weekly" -> "Weekly"
                    else -> "Specific days"
                }
            },
            onValueChange = { frequency = it },
        )

        if (frequency == "days") {
            FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                WEEKDAYS.forEachIndexed { index, day ->
                    FilterChip(
                        selected = index in selectedDays,
                        onClick = {
                            selectedDays = if (index in selectedDays) {
                                selectedDays - index
                            } else {
                                selectedDays + index
                            }.distinct()
                        },
                        label = { Text(day) },
                    )
                }
            }
        }

        // ---- publish mode ------------------------------------------------
        SectionTitle("Publishing")
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            FilterChip(
                selected = publishMode == "immediate",
                onClick = { publishMode = "immediate" },
                label = { Text("Publish immediately") },
            )
            FilterChip(
                selected = publishMode == "scheduled",
                onClick = { publishMode = "scheduled" },
                label = { Text("Schedule") },
            )
        }
        Text(
            when {
                // UNKNOWN comes first. A null means /health never answered,
                // and the two statements below are both claims about what
                // the backend will do with the video.
                forcePrivate == null ->
                    // NOT "nothing is uploaded until you approve it" - that
                    // was only ever true while everything was forced
                    // private, and in AUTO mode it is now false.
                    "Cannot reach the backend, so what happens on publish is " +
                        "unknown - it may publish publicly, or be set to " +
                        "force every upload private."
                forcePrivate == true ->
                    "The backend is in rehearsal mode: every upload stays " +
                        "private and nothing is scheduled, so publishing has " +
                        "no effect until that is turned off. Useful for " +
                        "checking that uploads work without subscribers " +
                        "seeing anything."
                publishMode == "immediate" ->
                    "Published PUBLICLY as soon as the video is approved - " +
                        "or straight away, if the mode below is Auto."
                else ->
                    "Handed to YouTube with a scheduled publish time, so your " +
                        "phone does not need to be online for it."
            },
            style = MaterialTheme.typography.bodySmall,
            color = when (forcePrivate) {
                null -> MaterialTheme.colorScheme.error
                true -> MaterialTheme.colorScheme.primary
                else -> MaterialTheme.colorScheme.onSurfaceVariant
            },
        )

        if (frequency != "once" && publishMode == "scheduled") {
            OutlinedTextField(
                value = uploadTime,
                onValueChange = { input ->
                    // Keep it to HH:MM; the backend validates too.
                    if (input.length <= 5) uploadTime = input
                },
                label = { Text("Publish time (24h, ${vm.store.timezone})") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )
            Text(
                "The video is produced ahead of time and handed to YouTube with a " +
                    "scheduled publish time, so your phone does not need to be " +
                    "online at ${uploadTime}.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }

        SectionTitle("Number of videos this run: $count")
        // Four is the honest ceiling: 10,000 free quota units a day, and a
        // published video spends 2,050 of them (1,600 to insert, 50 for the
        // thumbnail, 400 for the caption track). The backend refuses the
        // fifth, so a slider that offered it would be promising a video
        // that cannot be made.
        Slider(
            value = count.toFloat(),
            onValueChange = { count = it.roundToInt() },
            valueRange = 1f..5f,
            steps = 3,
        )

        // ---- mode -------------------------------------------------------
        SectionTitle("Mode")
        Row(verticalAlignment = Alignment.CenterVertically) {
            Switch(checked = autoMode,
                   onCheckedChange = { autoMode = it; autoModeTouched = true })
            Spacer(Modifier.height(0.dp))
            Column(Modifier.padding(start = 12.dp)) {
                Text(
                    if (autoMode) "AUTO - publish without asking"
                    else "APPROVAL - I review before publishing",
                    style = MaterialTheme.typography.bodyMedium,
                )
                Text(
                    if (autoMode) {
                        // Say the consequence. Every upload used to be
                        // pinned private, which made AUTO mean "waiting for
                        // you in Studio"; it now means live on the channel
                        // with nobody having watched it.
                        "The video goes LIVE on the channel without you " +
                            "seeing it first. The quality gate still blocks " +
                            "anything below your threshold, and anything the " +
                            "fact checker flags still waits for you."
                    } else {
                        "Recommended until you trust the output."
                    },
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }

        Row(verticalAlignment = Alignment.CenterVertically) {
            Switch(
                checked = madeForKids,
                // Locked when the audience or niche already decides it. A
                // switch you can turn off while the backend turns it back on
                // is worse than no switch: it looks like the setting took
                // effect when it did not.
                enabled = !kidsRequired,
                onCheckedChange = { madeForKids = it },
            )
            Column(Modifier.padding(start = 12.dp)) {
                Text("Made for Kids", style = MaterialTheme.typography.bodyMedium)
                Text(
                    when {
                        audienceIsChildren ->
                            "Required: the audience is under 13, so YouTube " +
                                "treats this as child-directed whatever else " +
                                "is set."
                        nicheIsKids ->
                            "Required: this niche is child-directed."
                        else ->
                            "Sets YouTube's child-directed classification. " +
                                "Required by law if the content targets children."
                    },
                    style = MaterialTheme.typography.bodySmall,
                    color = if (kidsRequired) MaterialTheme.colorScheme.primary
                            else MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }

        // Asked ONCE PER CHANNEL GROUP, not once per automation.
        //
        // The backend used to satisfy this from "has an earlier run of this
        // same automation published?" - and the Create screen mints a new
        // automation for every one-off video, so the answer was always no
        // and every kids video waited for approval however AUTO was set.
        if (madeForKids) {
            if (kidsAlreadyConfirmed) {
                Text(
                    "Made for Kids is already confirmed for this channel " +
                        "group, so this video will not wait for approval.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.primary,
                )
            } else {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Checkbox(
                        checked = kidsConfirmed,
                        onCheckedChange = { kidsConfirmed = it },
                    )
                    Text(
                        "I confirm this content is directed to children. " +
                            "YouTube disables comments, personalised ads and " +
                            "several other features on these videos, and an " +
                            "inaccurate classification has legal " +
                            "consequences. Asked once per channel group.",
                        style = MaterialTheme.typography.bodySmall,
                        modifier = Modifier.padding(start = 4.dp),
                    )
                }
            }
        }

        if (busy) LoadingRow("Queueing...")

        Button(
            onClick = {
                vm.start(
                    AutomationRequestDto(
                        niche = if (rotating) "" else niche.trim(),
                        topicRotate = rotating,
                        audience = audience,
                        language = language,
                        videoFormat = if (isShort) "SHORT" else "LONGFORM",
                        durationSeconds = lengthSeconds,
                        style = style,
                        voiceGender = voiceGender,
                        // Both derived by the backend now - the caption
                        // language from the voice language, the style from
                        // the niche. Sent blank rather than dropped, because
                        // the API still accepts an explicit override.
                        captionLanguage = "",
                        captionStyle = "",
                        scriptSource = scriptSource,
                        nicheGroup = groupKey,
                        channelId = channelId,
                        count = count,
                        mode = if (autoMode) "AUTO" else "APPROVAL",
                        frequency = frequency,
                        days = if (frequency == "days") selectedDays.sorted() else emptyList(),
                        uploadTime = if (frequency == "once" ||
                            publishMode == "immediate") "" else uploadTime,
                        publishMode = publishMode,
                        timezone = vm.store.timezone,
                        madeForKids = madeForKids,
                        // Only the tick actually given IN THIS session.
                        // OR-ing in kidsAlreadyConfirmed would assert a
                        // human act that did not happen here, and the
                        // backend would record a fresh confirmation for
                        // an automation nobody confirmed. The backend's
                        // own group record satisfies the gate already.
                        kidsConfirmed = kidsConfirmed,
                        // The Settings threshold, which was written to device
                        // preferences and then sent to nobody - so the slider
                        // and its explanatory sentence did nothing in either
                        // direction.
                        minQualityScore = vm.store.qualityThreshold,
                    )
                )
            },
            enabled = !busy && (rotating || niche.trim().length >= 2) &&
                (!madeForKids || kidsConfirmed || kidsAlreadyConfirmed),
            modifier = Modifier.fillMaxWidth(),
        ) {
            Text("START AUTOMATION")
        }

        Spacer(Modifier.height(32.dp))
    }

    // Kids confirmation (spec section 9): explicit, blocking, before anything
    // runs - but asked ONCE PER NICHE, not on every preview refresh. The
    // preview is re-requested whenever the niche, audience, style or length
    // changes, and the prompt was re-armed from its result each time, so the
    // dialog reappeared constantly. A consent dialog you have to dismiss
    // repeatedly stops being consent and becomes an obstacle.
    // kidsBlocked overrides the once-per-niche rule, because it is not the
    // advisory prompt: the backend has REFUSED the run. Suppressing it here
    // was a dead end - "No, general audience" marked the niche answered, the
    // backend kept returning 409, and the only thing on screen was
    // "Confirmation required (see the message on screen)" with no message
    // anywhere and no way to change the answer.
    val askKids = kidsBlocked || (
        kidsPrompt && !madeForKids && !nicheIsKids &&
            kidsAnsweredFor != niche.trim().lowercase()
        )
    if (askKids) {
        val answered = {
            kidsAnsweredFor = niche.trim().lowercase()
            vm.dismissKidsPrompt()
            vm.dismissKidsBlocked()
        }
        AlertDialog(
            onDismissRequest = {
                vm.dismissKidsPrompt()
                vm.dismissKidsBlocked()
            },
            title = {
                Text(
                    if (kidsBlocked) "This topic needs a Made-for-Kids answer"
                    else "Is this content for children?"
                )
            },
            text = {
                Text(
                    if (kidsBlocked) {
                        "The backend will not run this topic as general " +
                            "audience: it classifies it as child-directed, " +
                            "and YouTube requires the \"Made for Kids\" " +
                            "setting to match the real audience.\n\n" +
                            "Either turn it on, or change the topic to " +
                            "something not aimed at children."
                    } else {
                        "This niche looks child-directed. YouTube requires an " +
                            "accurate \"Made for Kids\" classification, and " +
                            "getting it wrong has legal consequences.\n\n" +
                            "Turning this on also enables a stricter safety " +
                            "profile: no scary or unsafe content, simpler " +
                            "language, and no commercial prompts."
                    }
                )
            },
            confirmButton = {
                TextButton(onClick = {
                    madeForKids = true
                    kidsConfirmed = true
                    answered()
                }) { Text("Yes, made for kids") }
            },
            dismissButton = {
                TextButton(onClick = answered) {
                    Text(
                        if (kidsBlocked) "Let me change the topic"
                        else "No, general audience"
                    )
                }
            },
        )
    }
}

private fun formatLength(seconds: Int): String =
    if (seconds >= 60) "${seconds / 60}m ${seconds % 60}s" else "${seconds}s"

/**
 * Rough render-time estimate.
 *
 * Measured end to end on the dev laptop: a 240-second long-form video took
 * about 62 minutes, i.e. roughly 15x realtime. The final encode and the
 * per-scene clips dominate; image generation adds more when a free provider
 * is rate limiting. Showing a number here stops a 20-minute request looking
 * like a hang. A machine with a GPU or more cores will beat it comfortably.
 */
private fun estimateRenderMinutes(seconds: Int): Int =
    ((seconds * 15.0) / 60).toInt().coerceAtLeast(2)
