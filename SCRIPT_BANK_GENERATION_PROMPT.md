# Script bank generation prompt

**Hand everything below the line to Gemini (or any capable model) and say:
"Generate scripts for this cell using this standard."**

---

## How to use this, and how to regenerate it

This file is **generated from the code that enforces it**:

```bash
python scripts/bank_spec.py --out SCRIPT_BANK_GENERATION_PROMPT.md
python scripts/bank_spec.py --group kids --target 50 --out kids-only.md
```

Regenerate it after any change to the importer, the beat tables, the
vocabularies or the share caps. A hand-edited copy drifts from the validator,
and the failure mode is three hundred entries that all fail import at once —
which is exactly why this is a program and not a document.

### Which cell to ask for

One cell = one `(group, language, format, topic)`. The importer derives the
group from the filename, so name files:

```
<group>-<lang>-<short|longform>-<topic-with-hyphens>.jsonl
e.g. kids-hi-short-kids-toys-and-play.jsonl
```

The 40 topics live in `engine/core/groups.py`: 9 kids, 2 finance, 29 tech.
The tech list deliberately spans far more than Excel and Office — YouTube
growth, PC/laptop, phone buying, AI concepts, science, SQL, programming,
developer tools, Windows/Android/iPhone/browser tips, Google Workspace,
shortcuts, troubleshooting, backup, cybersecurity, privacy, cloud storage,
networking, productivity and tech myths.

### On volume — read this before asking for 200

The target in this document is **50 per cell**, not 200, and that is a
measured decision rather than a compromise:

* Authoring was measured at **19,018 tokens per Short** and **110,527 per
  long-form entry**. 200 of each across 40 topics is roughly **87M + 508M
  tokens** — two orders of magnitude beyond any practical session.
* The share caps make a single 200-entry topic self-defeating anyway. They are
  computed over **group + language**, not per topic, so 200 narrative entries
  in one topic must still fit inside a 20% arc ceiling measured against every
  entry in that group and language. A large block written blind loses entries
  to `arc_share` no matter how good each one is — one real batch lost 20 that
  way.
* Batches of **10–20** land far better than one large block, because earlier
  entries move the shares and make room for later ones.

If you want 200 in a cell, get there in batches over time and check the spread
as you go. `python scripts/bank_fill.py plan` reports what is actually thin.

### After the model gives you a file

```bash
python scripts/bank_fill.py check  batch.jsonl    # gate it, store nothing
python scripts/bank_fill.py absorb batch.jsonl    # store what passes
python scripts/bank_rebuild.py --confirm          # reconcile and deliver
```

`check` first, always. It tells you what would be refused and why, before
anything touches the bank.

---

# Script bank - authoring spec

Everything a writing tool needs to produce entries this project will
accept. Generated from the validator itself, so it cannot drift from
what the importer enforces.

## What to produce

**JSONL: one JSON object per line.** No array wrapper, no markdown
fence, no numbering, no prose, no blank lines. Every line must parse
on its own. One file per cell, named:

```
<group>-<language>-<short|longform>-<topic-with-hyphens>.jsonl
e.g. kids-hi-short-kids-bedtime-stories.jsonl
```

Hand the files back and they are imported with:

```
python scripts/bank_rebuild.py --confirm
```

which gates every line, keeps what passes, and reports the rest.

## How many to write

**50 per cell** - that is 50 entries for each row of the
table below, so a topic that appears twice (English and Hindi) wants
50 of each, and Shorts and long-form are counted separately.

**Write them in batches of 10-20, not in one block.** This is not a
style preference, it is how the caps work: the variety limits at the
bottom of this file are shares of the WHOLE group, not of your batch.
A single large block written blind will have entries refused on
`arc_variant` exceeding 20% or
`outcome_class` exceeding 30%,
however well written each one is. Smaller batches let the earlier
ones land first, which moves the shares and makes room.

A rejected entry is not a small loss - it can never become a video.
Ten that import cleanly beat twenty where six are refused.

## READ THIS FIRST: what actually refuses entries

A 357-entry batch written against an earlier version of this spec
had 79 entries refused. They were not spread evenly - **69 of the 79
were one cause**, and it was not craft or safety. It was repeating
the same story shape.

### 1. Spread `arc_variant` and `outcome_class` - the counts, for 50 entries

| field | no more than | out of 50 |
|---|---|---|
| the same `arc_variant` | **10** | 20% |
| the same `outcome_class` | **15** | 30% |
| `turn_kind` of `notice` | **12** | 25% |

**These are shares of the whole GROUP AND LANGUAGE, not of your
topic and not of your batch.** `kids` + `hi` is one pool: every
Hindi kids entry already banked, across every topic, counts against
your 20%. The bank is not empty, so
treat the numbers above as a ceiling and aim well under them.

Doing this correctly in one language does NOT cover the other. The
third real batch is the proof - same topic, two languages, one
brief:

| cell | `alone` | outcome |
|---|---|---|
| kids / **en** / toys and play | 6 of 45, spread across all 8 arcs | **45 of 45 landed** |
| kids / **hi** / toys and play | **38 of 50 (76%)** | 11 refused on `arc_share` |

Nothing was wrong with those 11 scripts. Count your own arcs before
handing the file over:

```
python -c "import json,sys,collections;print(collections.Counter(json.loads(l).get('arc_variant') for l in open(sys.argv[1],encoding='utf-8') if l.strip()))" your-file.jsonl
```

If any single value is near a fifth of the file, rewrite some
before submitting - free to change now, impossible to recover
later.

**THE DEFAULT TRAP.** The obvious children's story is *the child
solves it alone and gets what they wanted* - `arc_variant: alone`
with `outcome_class: got_it`. In the real batch that was 42% and
43% respectively, which is more than double the cap, and it is why
69 good scripts were refused. Every value below is a different, real
story. Use them:

| `arc_variant` | the shape it names |
|---|---|
| `alone` | they work it out by themselves |
| `by_helping` | they get what they want by helping someone else |
| `cooperate` | they and another child solve it together |
| `reframe` | they solve a different problem than the one they started with |
| `wrong_want` | what they wanted turns out not to be the thing |
| `noticed` | someone notices their effort and it changes things |
| `granted_early` | they get it early and that creates the problem |
| `gave_it_away` | they end up giving it away |

| `outcome_class` | the ending it names |
|---|---|
| `got_it` | they get the thing they wanted |
| `got_better` | they get something better than they wanted |
| `changed_mind` | they stop wanting it |
| `helped_another` | someone else ends up better off |
| `gave_away` | they give it away and are glad |

### 2. The obstacle is an EVENT, not a sensation

This is a BLOCKING check and it refused nine entries. Something must
get measurably worse **in the world**: someone else takes the thing,
a limit appears, the attempt breaks something, a thing drops or
collapses or is knocked over.

What does NOT count: *his chest felt tight*, *her arms ached*, *he
was tired*. A feeling is a fine second sentence and it is not a
complication, because nothing about the situation has changed.

### 3. The turn is something they DO

Also blocking. They use a thing for a job it was not made for,
combine two things, trade, ask differently, or change what they
want. **Not** *she realised*, *he noticed*, *she saw that*. Two
entries were refused for writing a real invention as a realisation -
if the child combines two things, say that they combined them.

**Do not lead the sentence with the perception.** Four entries in
the third batch read "Tara noticed the grooved feet on each
figure, interlocking them into one tall tower" - the invention is
there, but it arrives after the looking. Put the action first:
"Tara interlocked the grooved feet into one tall tower." It reads
better and cannot be misread as a turn where the child only
looked.

### 4. These words will refuse your entry

The safety check is deliberately blunt, because it guards children's
content. It is not guessable, so here it is. A real batch lost a poem
about a seedling to the word *shoot*.

| avoid | why | say instead |
|---|---|---|
| `shoot` alone | reads as violence | `green shoot`, `new shoots`, and `shoot` beside `camera`/`lens`/`photo` are allowed |
| `knife` | reads as violence | `butter knife`, `plastic knife` (allowed); or avoid |
| `kiss` | reads as romance | no substitute - rewrite the line |
| `scary`, `monster`, `nightmare` | frightening | describe the dark, not a threat |
| `dead`, `die` | violence, unless the subject is a torch, lamp, battery or flame; `dead weight`, `dead end` and `stopped dead` are allowed | `the torch went out` |
| `rifle`, `pistol`, `revolver`, `shotgun`, `firearm`, `grenade`, `sniper`, `dagger`, `machete` | **blocked outright, no exceptions** - these were missing from the check entirely | leave weapons out of children's content |
| `stupid`, `idiot`, `shut up` | inappropriate | - |
| `buy now`, `click the link` | commercial pressure | - |

### 5. Every line must be valid JSON on its own

Malformed lines: three in the first real batch, two in the second,
and **five in the third**. The third had one repeatable cause worth
naming, because it is easy to produce and easy to miss - a scene
object whose `narration` KEY is missing while its value is still
there:

```
{"beat":"turn","<the narration text>","caption":...
                   ^ broken - no key
{"beat":"turn","narration":"<the narration text>","caption":...   correct
```

Those five were recovered, because the key order is identical in
all 940 well-formed scenes so the missing name was certain. Do not
rely on that - a break anywhere else, or a truncated line, loses
the entry. Before handing the file over, check every line parses:

```
python -c "import json,sys;[json.loads(l) for l in open(sys.argv[1],encoding='utf-8') if l.strip()]" your-file.jsonl
```

Silence means every line is good.

### 6. No two entries may share the same narration

An entry's identity is a hash of its narration text, nothing else -
not the title, not the topic, not the `on_screen_text`. Two entries
with byte-identical narration are therefore ONE entry, and the
second silently replaces the first.

The third batch lost 10 more - 145 lines holding only 135 distinct
scripts, each repeat under a fresh title.

The second real batch lost 15 entries this way: 14 speaking drills
repeated verbatim under different titles, and one pair where two
different Hindi spelling words were given the same script body. A
different title does not make a different entry. Check it:

```
python -c "import json,sys,collections;c=collections.Counter(chr(10).join(s['narration'] for s in json.loads(l)['scenes']) for l in open(sys.argv[1],encoding='utf-8') if l.strip());print([n for n,k in c.items() if k>1][:5] or 'all unique')" your-file.jsonl
```

Each `refrain` must be unique too - not just against the bank, but
within your own batch.

### 7. The rejection that was not a gate failure at all

1015 entries were reviewed. 893 were kept and **122 were rejected in
one sentence: "not up to the mark, not engaging".** Every one of
those 122 had already passed schema, story shape, the obstacle and
turn checks, safety, variety and the share caps. So sections 8 and 9
are not new taste - they are the habits the rejected batch had and
the 893 kept entries do not.

**Read the honesty note before you weight any of it.** The 122 were
one automated batch from one tool, and the verdict was a single
sentence over the whole file - there is ONE negative label, not 122.
Worse, every rejected entry came from that tool and no kept entry
did, so "rejected" and "written by that tool" are the same
variable. Measuring the two sets against each other finds the tool's
fingerprints as easily as any flaw: a space before the percent sign
("42 %") appears in 12 of the 122 and 0 of the 893, and it has
nothing to do with whether a script is engaging.

That is why almost nothing from that comparison became a gate, and
why section 8 is GUIDANCE with its numbers shown rather than a rule
you can be refused for. Section 9 is enforced, because it is an
entry contradicting itself rather than a matter of style.

### 8. Rhythm - GUIDANCE, not enforced

Four mechanical habits. Each one is measured against the whole bank,
and the point of quoting the kept column is that following these
cannot hurt you - 891 of the 893 kept entries already satisfy all
four:

| | rule | rejected batch | kept bank |
|---|---|---|---|
| 8a | at least one scene carries **two or more sentences** | 46/122 broke it | **0/893** |
| 8b | longest sentence at least **6 words longer** than the shortest *(poems exempt - metre is the point)* | 43/122 | 2/893 |
| 8c | mean narration of **9+ words per scene**, 11 is better | 61/122 | **0/893** |
| 8d | under **40% of scenes at 8 words or shorter** *(poems exempt)* | 55/122 | **0/893** |

Together they flag 93 of the 122 rejected entries (76%) and 2 of the
893 kept ones (0.2%). The short version: **the rejected batch was
chopped into single short sentences of one length.** Vary your
sentence lengths inside an entry and let a scene carry two thoughts.

Two findings are recorded here because they are the opposite of what
you would guess, and writing to them would make a batch worse:

- **A repeated opening formula is not the problem.** All 181 of the
  kept English kids drills begin with the identical three words
  ("Today we will") - the most formulaic opening in the whole
  dataset sits in the corpus that was kept.
- **Short openings are not the problem either.** A kept corpus opens
  shorter than the rejected one in both drill cells (median 6 and 5
  words against 7).

One thing to avoid outright, for tech: **do not open on a number.**
A percentage in the first spoken line appears in 13 of the 122 and 0
of the 893; a spec figure of any kind in 25 of 122 (39% of tech
explainers) against 2 of 276. Note the system's own hook scorer
REWARDS a digit in the opening - it is calibrated for a different
kind of video, and on this batch it scored the rejected entries
higher than the kept ones. Ignore it and open on the thing itself.

### 9. A refrain has to BE a refrain - ENFORCED, every shape

Sections above ask for a verbatim refrain in narrative and poem. It
is now checked for **any** shape that fills the `refrain` field,
because drills were never asked and it showed: 11 of the 32 rejected
entries with a declared refrain spoke it ONCE, against 0 of the 721
kept ones. A line said once is not a refrain.

> **Blocking:** if you fill `refrain`, that exact string must appear
> in the narration of **at least two scenes** - and **three** if
> the shape is `narrative` or `poem`, where the older story gate has
> always required three and still does. Two is the floor for every
> other shape. If the shape has no
> refrain, leave the field empty - an empty field is never checked.

**Do not let the refrain grade the child.** All 10 rejected Hindi
drills had a refrain containing सही or बिल्कुल - "क क क, बिल्कुल सही!" ("k k k, absolutely correct!") -
against 0 of the 721 kept. The refrain is the line the child says
ALONG WITH the narrator, so a refrain asserting the answer was right
lands before they have answered. This one warns rather than blocks:
a celebration line after the answer is a fine choice, and only using
it as the repeated refrain is the mistake.

## The fields

| field | when | requirement |
|---|---|---|
| `group` | REQUIRED | kids | finance | tech. Must match the file. |
| `topic` | REQUIRED | EXACTLY one of the group's topics, spelled character for character. A misspelling does not fail - it makes the entry reachable only by a group-wide automation, which is a silent loss. |
| `shape` | REQUIRED | narrative | drill | poem | explainer | procedure. Decided by the topic; see the per-cell table. |
| `volatility` | REQUIRED | evergreen | seasonal | live_only. Use "evergreen" - anything else is not worth banking. |
| `language` | REQUIRED | en | hi. The NARRATION language. |
| `video_format` | REQUIRED | SHORT | LONGFORM. |
| `made_for_kids` | REQUIRED | true for every kids entry, false otherwise. Not a preference - it selects a stricter safety profile. |
| `title` | REQUIRED | See TITLES below. Max 100 characters INCLUDING emoji and hashtags. |
| `title_alts` | REQUIRED | Two more titles in the same band. |
| `refrain` | narrative/poem/drill | The line repeated word for word. Must be concrete - a thing a child can point at, not an idea. |
| `description_hook` | REQUIRED | One or two sentences that open the YouTube description. Not the narration. |
| `arc_variant` | narrative/poem | alone | by_helping | reframe | wrong_want | cooperate | noticed | granted_early | gave_it_away |
| `outcome_class` | narrative/poem | got_it | got_better | gave_away | changed_mind | helped_another |
| `turn_kind` | narrative/poem | invent | combine | trade | ask | reframe | notice. What the child DOES at the turn. 'notice' is capped - see the caps table. |
| `problem_domain` | REQUIRED | Short lowercase label, e.g. "shadow play", "water carrying". |
| `setting` | REQUIRED | Short lowercase label, e.g. "shared bedroom at night". |
| `protagonist_type` | REQUIRED | Short lowercase label, e.g. "five-year-old girl". |
| `emotional_register` | REQUIRED | Short lowercase label, e.g. "calm and curious". |
| `characters` | narrative/poem | List of {"name", "description"}. The description is age, hair, clothing and ONE distinguishing feature - it is what keeps the pictures consistent. |
| `claims` | any numeric figure | List of {"claim", "confidence", "basis"}. EVERY number the narration asserts must appear here, and an illustrative figure must say so in its basis AND in the narration. |
| `scenes` | REQUIRED | 3-400 objects; see the per-cell table for the real range. |
| `scenes[].beat` | REQUIRED | One of the beat names for this shape, in order. Several consecutive scenes MAY share a beat - that is what makes a long-form entry's chapters. |
| `scenes[].narration` | REQUIRED | What the voice says, in the narration language. |
| `scenes[].caption` | REQUIRED | The SAME MEANING in the OTHER language. Max 90 characters. |
| `scenes[].image_brief` | REQUIRED | One picture, ALWAYS IN ENGLISH. See IMAGE BRIEFS. |
| `scenes[].on_screen_text` | optional | Short overlay, or "". |

## Cells, with their exact word and scene budgets

**Word counts are the contract, not durations.** Narration pace is
fixed per group and topic, so the word count IS the length. Count the
words before you output an entry.

| group | topic | lang | format | shape | words | scenes | words/sec |
|---|---|---|---|---|---|---|---|
| kids | kids bedtime stories | en | SHORT | narrative | 92-112 | 6-8 | 2.0 |
| kids | kids bedtime stories | en | LONGFORM | narrative | 883-1075 | 90-90 | 2.0 |
| kids | kids bedtime stories | hi | SHORT | narrative | 92-112 | 6-8 | 2.0 |
| kids | kids bedtime stories | hi | LONGFORM | narrative | 883-1075 | 90-90 | 2.0 |
| kids | kids moral stories | en | SHORT | narrative | 92-112 | 6-8 | 2.0 |
| kids | kids moral stories | en | LONGFORM | narrative | 883-1075 | 90-90 | 2.0 |
| kids | kids moral stories | hi | SHORT | narrative | 92-112 | 6-8 | 2.0 |
| kids | kids moral stories | hi | LONGFORM | narrative | 883-1075 | 90-90 | 2.0 |
| kids | kids rhymes and poems | en | SHORT | poem | 92-112 | 7-9 | 2.0 |
| kids | kids rhymes and poems | en | LONGFORM | poem | 883-1075 | 90-90 | 2.0 |
| kids | kids rhymes and poems | hi | SHORT | poem | 92-112 | 7-9 | 2.0 |
| kids | kids rhymes and poems | hi | LONGFORM | poem | 883-1075 | 90-90 | 2.0 |
| kids | kids alphabet learning | en | SHORT | drill | 92-112 | 8-10 | 2.0 |
| kids | kids alphabet learning | en | LONGFORM | drill | 883-1075 | 90-90 | 2.0 |
| kids | kids alphabet learning | hi | SHORT | drill | 92-112 | 8-10 | 2.0 |
| kids | kids alphabet learning | hi | LONGFORM | drill | 883-1075 | 90-90 | 2.0 |
| kids | kids numbers and counting | en | SHORT | drill | 92-112 | 8-10 | 2.0 |
| kids | kids numbers and counting | en | LONGFORM | drill | 883-1075 | 90-90 | 2.0 |
| kids | kids numbers and counting | hi | SHORT | drill | 92-112 | 8-10 | 2.0 |
| kids | kids numbers and counting | hi | LONGFORM | drill | 883-1075 | 90-90 | 2.0 |
| kids | kids words and spelling | en | SHORT | drill | 92-112 | 8-10 | 2.0 |
| kids | kids words and spelling | en | LONGFORM | drill | 883-1075 | 90-90 | 2.0 |
| kids | kids words and spelling | hi | SHORT | drill | 92-112 | 8-10 | 2.0 |
| kids | kids words and spelling | hi | LONGFORM | drill | 883-1075 | 90-90 | 2.0 |
| kids | kids sentences and speaking | en | SHORT | drill | 92-112 | 8-10 | 2.0 |
| kids | kids sentences and speaking | en | LONGFORM | drill | 883-1075 | 90-90 | 2.0 |
| kids | kids sentences and speaking | hi | SHORT | drill | 92-112 | 8-10 | 2.0 |
| kids | kids sentences and speaking | hi | LONGFORM | drill | 883-1075 | 90-90 | 2.0 |
| kids | kids toys and play | en | SHORT | narrative | 92-112 | 6-8 | 2.0 |
| kids | kids toys and play | en | LONGFORM | narrative | 883-1075 | 90-90 | 2.0 |
| kids | kids toys and play | hi | SHORT | narrative | 92-112 | 6-8 | 2.0 |
| kids | kids toys and play | hi | LONGFORM | narrative | 883-1075 | 90-90 | 2.0 |
| kids | kids shapes and colours | en | SHORT | drill | 92-112 | 8-10 | 2.0 |
| kids | kids shapes and colours | en | LONGFORM | drill | 883-1075 | 90-90 | 2.0 |
| kids | kids shapes and colours | hi | SHORT | drill | 92-112 | 8-10 | 2.0 |
| kids | kids shapes and colours | hi | LONGFORM | drill | 883-1075 | 90-90 | 2.0 |
| finance | personal finance | en | SHORT | explainer | 115-140 | 11-13 | 2.5 |
| finance | personal finance | en | LONGFORM | explainer | 1104-1344 | 90-90 | 2.5 |
| tech | youtube tips and growth | en | SHORT | explainer | 124-151 | 11-13 | 2.7 |
| tech | youtube tips and growth | en | LONGFORM | explainer | 1192-1451 | 90-90 | 2.7 |
| tech | pc and laptop tech | en | SHORT | explainer | 124-151 | 11-13 | 2.7 |
| tech | pc and laptop tech | en | LONGFORM | explainer | 1192-1451 | 90-90 | 2.7 |
| tech | phone and laptop buying advice | en | SHORT | explainer | 124-151 | 11-13 | 2.7 |
| tech | phone and laptop buying advice | en | LONGFORM | explainer | 1192-1451 | 90-90 | 2.7 |
| tech | excel tips and tricks | en | SHORT | procedure | 115-140 | 11-13 | 2.5 |
| tech | excel tips and tricks | en | LONGFORM | procedure | 1104-1344 | 90-90 | 2.5 |
| tech | ms office tips and tricks | en | SHORT | procedure | 115-140 | 11-13 | 2.5 |
| tech | ms office tips and tricks | en | LONGFORM | procedure | 1104-1344 | 90-90 | 2.5 |
| tech | AI explained | en | SHORT | explainer | 124-151 | 11-13 | 2.7 |
| tech | AI explained | en | LONGFORM | explainer | 1192-1451 | 90-90 | 2.7 |
| tech | AI tools and courses | en | SHORT | explainer | 124-151 | 11-13 | 2.7 |
| tech | AI tools and courses | en | LONGFORM | explainer | 1192-1451 | 90-90 | 2.7 |
| tech | science facts | en | SHORT | explainer | 133-162 | 11-13 | 2.9 |
| tech | science facts | en | LONGFORM | explainer | 1280-1559 | 90-90 | 2.9 |
| tech | science experiments | en | SHORT | explainer | 133-162 | 11-13 | 2.9 |
| tech | science experiments | en | LONGFORM | explainer | 1280-1559 | 90-90 | 2.9 |
| tech | sql and databases | en | SHORT | explainer | 115-140 | 11-13 | 2.5 |
| tech | sql and databases | en | LONGFORM | explainer | 1104-1344 | 90-90 | 2.5 |
| tech | programming and coding | en | SHORT | explainer | 115-140 | 11-13 | 2.5 |
| tech | programming and coding | en | LONGFORM | explainer | 1104-1344 | 90-90 | 2.5 |
| tech | developer tools | en | SHORT | explainer | 115-140 | 11-13 | 2.5 |
| tech | developer tools | en | LONGFORM | explainer | 1104-1344 | 90-90 | 2.5 |
| tech | windows tips and tricks | en | SHORT | procedure | 124-151 | 11-13 | 2.7 |
| tech | windows tips and tricks | en | LONGFORM | procedure | 1192-1451 | 90-90 | 2.7 |
| tech | android tips and tricks | en | SHORT | procedure | 124-151 | 11-13 | 2.7 |
| tech | android tips and tricks | en | LONGFORM | procedure | 1192-1451 | 90-90 | 2.7 |
| tech | iphone tips and tricks | en | SHORT | procedure | 124-151 | 11-13 | 2.7 |
| tech | iphone tips and tricks | en | LONGFORM | procedure | 1192-1451 | 90-90 | 2.7 |
| tech | browser tips and tricks | en | SHORT | procedure | 124-151 | 11-13 | 2.7 |
| tech | browser tips and tricks | en | LONGFORM | procedure | 1192-1451 | 90-90 | 2.7 |
| tech | google tools and workspace | en | SHORT | explainer | 133-162 | 11-13 | 2.9 |
| tech | google tools and workspace | en | LONGFORM | explainer | 1280-1559 | 90-90 | 2.9 |
| tech | hidden features and shortcuts | en | SHORT | procedure | 124-151 | 11-13 | 2.7 |
| tech | hidden features and shortcuts | en | LONGFORM | procedure | 1192-1451 | 90-90 | 2.7 |
| tech | computer troubleshooting | en | SHORT | procedure | 124-151 | 11-13 | 2.7 |
| tech | computer troubleshooting | en | LONGFORM | procedure | 1192-1451 | 90-90 | 2.7 |
| tech | file management and backup | en | SHORT | procedure | 124-151 | 11-13 | 2.7 |
| tech | file management and backup | en | LONGFORM | procedure | 1192-1451 | 90-90 | 2.7 |
| tech | cybersecurity basics | en | SHORT | explainer | 124-151 | 11-13 | 2.7 |
| tech | cybersecurity basics | en | LONGFORM | explainer | 1192-1451 | 90-90 | 2.7 |
| tech | privacy and online safety | en | SHORT | explainer | 124-151 | 11-13 | 2.7 |
| tech | privacy and online safety | en | LONGFORM | explainer | 1192-1451 | 90-90 | 2.7 |
| tech | cloud storage explained | en | SHORT | explainer | 124-151 | 11-13 | 2.7 |
| tech | cloud storage explained | en | LONGFORM | explainer | 1192-1451 | 90-90 | 2.7 |
| tech | networking basics | en | SHORT | explainer | 124-151 | 11-13 | 2.7 |
| tech | networking basics | en | LONGFORM | explainer | 1192-1451 | 90-90 | 2.7 |
| tech | productivity software and apps | en | SHORT | explainer | 124-151 | 11-13 | 2.7 |
| tech | productivity software and apps | en | LONGFORM | explainer | 1192-1451 | 90-90 | 2.7 |
| tech | tech myths busted | en | SHORT | explainer | 124-151 | 11-13 | 2.7 |
| tech | tech myths busted | en | LONGFORM | explainer | 1192-1451 | 90-90 | 2.7 |
| tech | youtube automation and monetisation | en | SHORT | explainer | 124-151 | 11-13 | 2.7 |
| tech | youtube automation and monetisation | en | LONGFORM | explainer | 1192-1451 | 90-90 | 2.7 |

## Beats, per shape

Use these names, in this order. Several consecutive scenes may share
one beat name in a long-form entry - that is what becomes the chapter
list. A Short usually has one scene per beat.

### narrative

- **want** - name the character in the first five words and the ONE thing they want
- **attempt** - they try it THEMSELVES and it does not work
- **obstacle** - something gets measurably WORSE - a second person who wants it too, a limit appearing, or the attempt breaking something. Not a feeling in the body
- **turn** - they THEMSELVES do something new: use a thing for a job it was not made for, combine two things, trade, ask differently, or change what they want. NOT 'they noticed'
- **resolve** - they get there, and one warm line of how that feels
- **refrain** - the refrain, word for word, as the last line

### poem

- **open** - the picture the whole rhyme is about, in one line
- **verse_a** - four lines, an AABB or ABAB rhyme, all one action
- **refrain** - the refrain, word for word
- **verse_b** - four more lines - a NEW action, not the same one again
- **refrain_2** - the refrain again, word for word, identical
- **verse_c** - four lines that wind down; slower, quieter words
- **close** - the refrain one last time as the final line

### drill

- **open** - name the one thing we will learn today; NOT a question
- **item_intro** - the letter/number/shape, its sound and its form, said twice
- **model** - one everyday object as an example, named concretely
- **call** - invite the child to say it aloud with you
- **response** - say the answer back in the SAME words every time - this is the refrain slot
- **vary** - a second and third example from a DIFFERENT domain than the first
- **check** - a two-choice question the child can answer aloud, then the answer
- **recap** - list what was covered, in order, then the closing refrain

### explainer

- **hook** - the strongest single fact, stated cold
- **context** - why this matters to the viewer
- **promise** - what they will understand by the end
- **mechanism** - HOW it works - one causal chain, no lists
- **worked_example** - one concrete example with real numbers
- **boundary** - when it does NOT apply, or the common mistake
- **payoff** - the conclusion that reframes the hook
- **cta** - one specific next action

### procedure

- **hook** - the symptom the viewer already has
- **why** - what causes it, in one causal chain
- **prepare** - what they need before starting
- **steps** - the steps in order, each one checkable
- **verify** - how they know it worked
- **boundary** - when to stop and not attempt it
- **cta** - one specific next action

## Craft rules that are actually enforced

These are checks, not advice. An entry that breaks a BLOCKING one is
rejected and can never become a video.

- **BLOCKING - the turn is something the character DOES.** They use a
  thing for a job it was not made for, combine two things, trade, ask
  differently, or change what they want. NOT "they noticed" or
  "they saw". The test: could a five-year-old copy the idea
  tomorrow? Ten of the first nineteen stories failed this.
- **BLOCKING - the obstacle is more than a body feeling.** Something
  must get measurably WORSE in the world: a second person who wants
  the same thing, a limit appearing, the attempt breaking something.
  NOT "her chest felt tight". Twelve of the first nineteen failed
  this, and "throat went tight" appeared verbatim in three.
- **BLOCKING - the refrain is concrete.** A child can point at a ball
  and chant a count; they cannot point at kindness or patience.
- **BLOCKING - a verbatim refrain, at least three times**, identical
  character for character, and it is the last line.
- **BLOCKING - no scene opens on a rhetorical question.**
- Name the character in the first five words, and keep the name in
  roughly two thirds of the scenes. 31% is too few.
- About two join-in lines per entry, phrased as a sentence-initial
  imperative ("Count with me: one, two, three.").
- No adult rescue. The child solves it.

## Titles

- 55-70 VISIBLE characters, 8-14 words, and **under 100 characters in
  total** including the emoji and the hashtag tail. Devanagari titles
  have overrun this - 101 to 139 characters - and YouTube truncates.
- Two parts, joined by `...` or a question.
- **Withhold the ending.** A title that states the outcome leaves
  nothing to click for. No content word in the title may first appear
  in the last 40% of the scenes.
- One leading emoji, and a short lowercase hashtag tail.
- `title_alts` are two more in the same band, not throwaways.

## Captions

- The caption is the same meaning in the **other** language from the
  narration: English narration takes Devanagari captions, and the
  reverse. That is the entire point of them.
- Max 90 characters, so one line fits the frame.
- **Devanagari spelling has to be right** - captions are burned into
  the video, so an error is permanent. It is `एक`, `एआई`, `एप` - not
  `ऐक`, `ऐआई`, `ऐप` - and keep the nukta: `सिर्फ़`, `ज़रूरी`, `फ़ोन`.
  Both of those errors have shipped.
- Write Hindi in Devanagari, never romanised. Everyday loanwords stay
  loanwords in Devanagari (`लैपटॉप`, `रैम`, `बैटरी`); do not invent
  Sanskrit substitutes. Numbers and times as words, not digits.

## Image briefs

- One picture per scene, **always in English**, even when the
  narration is not.
- Detailed enough for an image model with no other context: who is in
  frame, what they are doing, where, the camera framing, the light,
  the mood. Never "show a computer".
- Describe each recurring character or object **identically every
  time** - that is what keeps the pictures consistent across scenes.
- **Every brief in an entry must be different.** Ninety near-identical
  briefs produce ninety near-identical pictures, which is the most
  boring thing this pipeline can output.
- Content only. No art-style, lens or camera-brand language - the
  render template owns the look.
- No presenter, no narrator, no talking head. An explainer has no
  cast.

## Variety caps, computed across the whole group

These are why a good entry can still be rejected: a share is a
property of the catalogue, not of the entry. They loosen as a group
grows, so a rejected entry often lands on a later pass.

| cap | limit |
|---|---|
| arc_variant share | <= 20% of the group |
| outcome_class share | <= 30% |
| turn_kind 'notice' share | <= 25% |
| diversity axes | >= 2 of the 7 axes must differ from EVERY other entry in the group |
| refrain | must be unique across the group |
| narration overlap | no near-duplicate wording against any banked entry |

The seven axes are: `arc_variant`, `outcome_class`, `turn_kind`,
`problem_domain`, `setting`, `protagonist_type`, `emotional_register`.
Two entries that differ only in the character's name are the same
story and will be refused.

## Also enforced

- **Evergreen only.** No dates, version numbers, prices, current
  events, launches or anything that will read as stale in two years.
- **Original.** Learn from what works; copy no real creator's script,
  title, storyline or distinctive wording.
- **Kids safety.** No weapons, injury, fear, or anything a parent
  would object to. A hand-written story once said "there was a knife
  in the kitchen" and was correctly refused.
- **Finance.** No first person, no persona, no advice. Illustrative
  figures must be declared in `claims` AND called illustrative in the
  narration.
- Do not include `provenance` or `human` - the importer fills those,
  and a file that claims a human review nobody performed is worse
  than one that claims nothing.

## A complete example line

Reformatted here for reading. In the file it is **one line**.

```json
{
  "group": "kids",
  "topic": "kids bedtime stories",
  "shape": "narrative",
  "volatility": "evergreen",
  "language": "hi",
  "video_format": "SHORT",
  "made_for_kids": true,
  "title": "🌙 नीलू की टॉर्च बुझी, कहानी का आख़िरी पन्ना अब कैसे पढ़ेगी वह? #kahani",
  "title_alts": [
    "🔖 नीलू का आख़िरी पन्ना अंधेरे में रह गया... अब क्या? #kahani",
    "💡 टॉर्च बुझने पर नीलू ने रोशनी कहाँ से ढूँढी? #kahani"
  ],
  "refrain": "एक पन्ना, एक पन्ना, और एक",
  "description_hook": "नीलू को कहानी का अंत जानना था, और टॉर्च ने साथ छोड़ दिया।",
  "arc_variant": "alone",
  "outcome_class": "got_it",
  "turn_kind": "invent",
  "problem_domain": "reading in the dark",
  "setting": "bedroom at night",
  "protagonist_type": "six-year-old girl",
  "emotional_register": "determined and calm",
  "characters": [
    {
      "name": "नीलू",
      "description": "six-year-old girl, short bobbed black hair, red star-print pyjamas, a chipped front tooth"
    }
  ],
  "claims": [],
  "scenes": [
    {
      "beat": "want",
      "narration": "अंत जानना था नीलू को, वरना सपना अधूरा।",
      "caption": "Nilu needed the ending tonight.",
      "image_brief": "Nilu, a six-year-old girl with short bobbed black hair and red star-print pyjamas, kneeling on a low bed with an open picture book, one hand holding a small yellow torch, warm lamplight from the left, quiet and intent",
      "on_screen_text": ""
    }
  ]
}
```

The `scenes` list is shown with one entry for brevity - a real one
carries the full count from the table above, every beat in order.
