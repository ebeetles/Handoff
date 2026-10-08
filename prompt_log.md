# Prompt log: Handoff

How Handoff was built, start to finish: which AI tools did what, my prompts (quoted **verbatim**
from the Claude Code session transcripts, typos included), what each one produced, and what I did
myself. Times are local (PDT). Very long pasted text that wasn't mine (tool instructions, the
assignment's own wording) is left out.

Prompts 1–3 are the exception to "my prompts": they were drafted outside Claude Code, during
planning, and pasted in (the transcripts mark them as pasted). They're quoted exactly as sent and
labelled. Every other prompt I typed myself.

Original Claude chat session, from brainstorming to idea to roadmap: https://claude.ai/share/cb6ceeb7-23d6-4c41-bc0e-1bd0b33bdae2

---

## Tools and models: which tool for which job

- **Claude Code** (Anthropic's agentic coding CLI, running **Claude Opus 5.5**) did almost all the
  work in the repository: implementing each chunk, debugging, writing and running tests, driving a
  real browser (Playwright) to check audio timing and UI, and preparing the deployment. I chose it
  for this because it works inside the project. It can run the pipeline, the test suites and the
  app, so it can *check* its own changes against `AGENTS.md`'s "done means tested" rules instead
  of just proposing code for me to paste.
- **OpenAI Codex** wrote the first version of the live AI composer (backend endpoint and the
  Compose panel). It ran out of usage limits halfway, so I handed the half-finished work to Claude
  Code to audit and finish (Prompt 16). Having a second tool write a piece was useful: Claude Code
  had to review and test someone else's code before trusting it.
- **Claude through the Anthropic API** (`claude-opus-5-5`) is part of the product: the AI composer
  sends it each pair's musical facts and gets back transition plans as structured JSON. I used the
  most capable model there, because the task is creative reasoning over a lot of structured musical
  data, and every reply must be valid against a strict schema.
- **A Claude chat (claude.ai)** for brainstorming, turning the idea into a plan, and writing the
  roadmap (linked above). The first structured spec prompts (1–3) were drafted outside Claude Code
  during planning and pasted in.
- **For questions and decisions,** like how human DJs handle key clashes, Vercel vs Render, or
  whether the music licences allow hosting, I asked Claude Code conversationally first and only let
  it build once I'd decided (Prompts 17, 28–31).

## How I worked

- **Plan first:** `ROADMAP.md` (architecture, contracts, chunks with "definition of done") and
  `AGENTS.md` (rules: one chunk at a time, contracts are the seams, done means tested, don't guess
  external APIs) came first, and every session works from them.
- **Precise prompts for engineering:** the first well-defined tasks went in as detailed spec prompts
  with a test plan and "don't commit, I'll review", drafted during planning and pasted (Prompts 1–3).
  After that, I typed my prompts directly.
- **Plain feedback for feel and sound:** for the hand controls and the music, I used it myself and
  described what felt or sounded wrong (Prompts 5–10, 17–22, 25, 27).
- **Reviewing before committing:** I reviewed diffs, tried each change on the board, and committed
  myself. The 10 commits run from `f71ee0b` (initial, Oct 3) to `9a3bfce` (Oct 7).

## What I did myself

- **Direction and design decisions:**
  - composing transitions from building blocks instead of picking presets;
  - hybrid lock-on for knobs only;
  - one refined transition instead of four;
  - key lock;
  - which tracks to keep;
  - a private, access-code deployment for course staff.
- **All hands-on testing, with a webcam and by ear:** every hand-control round (Prompts 5–10), and
  judging transitions (Prompts 17–18, 21–22), including overruling the AI's own scoring (see the
  last section).
- **Finding bugs in use:** duplicate menu entries, keys swapping back, B-into-A confusion, a cut-off
  countdown, dead top-bar controls, the window not shrinking (Prompts 25, 27), and the deployment
  CORS error (Prompts 33–35).
- **Accounts and deployment:** setting up Render and Cloudflare R2, entering the secrets, and
  uploading the library.
- **Writing the README** in my own words, from the scaffold in Prompt 36.

---

## Development process, start to finish

### 1. The plan and the first board (Oct 3)

https://claude.ai/share/cb6ceeb7-23d6-4c41-bc0e-1bd0b33bdae2
- `ROADMAP.md` and `AGENTS.md`;
- the typed contracts between the parts;
- the Python analysis pipeline (beat grid, key, sections, phrases), checked against synthetic
  ground-truth tracks;
- the Web Audio engine (decks, sync, loops, quantized launch), with timing tests;
- the two-deck board UI.

### 2. Real tracks: Audius downloader and beat-grid calibration (Oct 3, evening)

**Prompt 1** · Oct 3, 8:30 PM · *pasted: drafted outside Claude Code, not typed by me*

```text
Add pipeline/fetch_audius.py (part of Chunk 1). Read the current Audius
API docs at docs.audius.org first; use the swagger.yaml, don't guess
endpoints. It should:
- take track URLs/IDs, or a search query + optional genre, with --limit
- only download tracks where downloadable is true; skip and report others
- save to pipeline/tracks/ as "Artist - Title.<ext>"
- write license/credit (artist name + Audius track URL) into
  pipeline/overrides.json without clobbering existing entries
- read an optional AUDIUS_API_KEY from the environment (never hardcode it)
Test it against the live API with 3 real tracks, then run preprocess.py on
them and show me the output table. Add the usage to the README.
```

**Result:** `fetch_audius.py`, written from Audius's swagger spec. It downloads only tracks whose
artists allow it, and records each track's licence and credit.

**Prompt 2** · Oct 3, 8:56 PM · *pasted: drafted outside Claude Code, not typed by me*

```text
Work on ROADMAP Chunk 1b. I've put real tracks in pipeline/tracks/.
Run preprocess.py on them (no --stems yet) and give me a table per track:
BPM, grid type, grid_residual_ratio, beatmatchable, downbeat_confidence,
key, number of sections. Flag tracks that look suspicious and say why.
Do NOT change any thresholds yet. Propose changes with evidence and wait
for me. Start the dev server so I can spot-check phrase lines in the
board myself.
```

**Result:** a per-track table, which turned up three suspicious tracks (one mis-tracked tempo,
one borderline, one odd sectioning). No thresholds were changed.

**Prompt 3** · Oct 3, 9:12 PM · the main calibration task · *pasted: drafted outside Claude Code, not typed by me*

```text
Context: we're in ROADMAP Chunk 1b (calibrating the analysis pipeline on
real tracks). A previous session ran preprocess.py --force on the library
and found the following. No code or thresholds were changed. Start by
checking git status/log and reproducing these findings before changing
anything.

Findings:
1. "Symbiotic Crowd - Frog Prince" is mis-analyzed: tempo 123.965, tracked
   grid, grid_residual_ratio 0.248, downbeat conf 0.12, 12 sections. The
   track is a steady 124 BPM (the title says so; an exact 124.000 grid stays
   within ±1 ms in every quarter). The beat tracker slips half a beat about
   4 times (near 15 s, 108 s, 170 s, 248 s), so the least-squares fit
   t_i = a + b*i gets wrong indices after each slip: residuals split into
   two clusters half a beat apart and the slope is biased. Overrides can't
   fix it (force_grid regular + bpm_hint 124 still drifts ~100 ms).
2. "Zute - Nonstop" passed beatmatchable only barely (residual 0.038 vs the
   0.04 cutoff). All outliers are 3 tracker start-up beats in the first
   0.9 s; the grid itself drifts ≤ 4 ms.
3. "djteixo - Tropical House" has 9 sections, several 8 bars long, with
   boundaries at bars 36/44/92, off the 16-bar phrase lines. Possibly a
   4-bar phrase offset. Not yet checked by ear; don't change section logic.

What to do (plan first, show me the plan, then implement):

A. Replace the grid fit with a direct tempo/phase search that never uses
   the tracker's beat indices: score candidate grids
   S(a, b) = sum_i env(a + b*i), with b swept in a narrow band around the
   tracker's estimate and a over one full beat period. Score against the
   low-band (kick) onset envelope, or weight it heavily, so the phase
   search can't lock onto loud offbeat hats. Keep the existing high-res
   phase refinement afterwards if it still helps. Write the failing test
   first: synthetic tracks with half-beat tracker slips AND loud offbeat
   hats. The existing ground-truth tests must still pass.

B. Redefine "beatmatchable" by what matters: split the track into
   segments, find the best phase offset of the final global grid in each,
   and record the max drift in ms. Beatmatchable = max drift below a
   threshold (start ~10 ms; explain your choice). This replaces
   grid_residual_ratio as the decision metric. It's a contract change:
   bump the schema to v2, update contracts/track_analysis.schema.json,
   web/src/contracts/track.ts, the fixtures, and anything that reads the
   tempo fields. Log the decision in ROADMAP.md.

C. Don't change BEATMATCHABLE thresholds beyond B, and don't touch the
   section detection yet.

D. Check whether any pipeline tests hit the network (e.g. Audius). If
   they do, mock them so CI isn't flaky.

Done = all checks in AGENTS.md pass, including web typecheck/tests and
e2e/smoke.py (start the dev server in the background), since the schema
changes. Then rerun preprocess.py --force and show me the same table as
before (BPM, grid, drift ms, beatmatchable, downbeat conf, key, sections),
with Frog Prince fixed. Paste test output. Don't commit; I'll review the
diff.
```

**Result:**
- **New beat grid:** a direct kick-weighted grid search replaced the line fit through tracked
  beats, which a half-beat tracker slip broke.
- **New "beatmatchable" rule:** it now means the worst drift is ≤ 10 ms (analysis format v2).
- **Tests first:** new synthetic tests for loud offbeat hats and tracker slips.
- **Frog Prince** now reads as a steady 124 BPM.

**Prompt 4** · Oct 3, 10:05 PM

```text
Everything looks good when I tested it out, let's proceed with next step implementation
```


### 3. Hand control: six rounds of feel tuning (Oct 4)

The first version (MediaPipe hand landmarks → the same gesture system the mouse uses) was tested
on synthetic hands. Then I used it with my own hands and fed back each round:

**Prompt 5** · Oct 4, 11:28 AM

```text
Some behaviors I would like to change in terms of the hand controls. The pinching is a little inconsistent, I have to really clearly show my thumb and index fingers for it to work. It is just a little hard to use in general, maybe we can have a little bit of lock on, when we get pretty close to a knob or button we can have the cursor lock onto it. With the lock on, we can also make the knobs actually twistable instead of just using up and down, which would add a lot better feel to the experience.
```

**Result (v0.2):** a looser pinch, lock-on to nearby controls, and twistable knobs (palm rotation
turns them).

**Prompt 6** · Oct 4, 11:46 AM

```text
i really like the knob twisting, works pretty well. The lock-on is too strict in my opinion, which is an issue especially because all the buttons and knobs and sliders are so close together.  and also the pinching is now too forgiving. I want it to only click if my index is basically touching my thumb. right now if my fingers are just kind of relaxed it would register as a press since it is close enough.
```

**Result (v0.3):** a strict pinch (fingertips touching) and a lock-on that follows the hand.

**Prompt 7** · Oct 4, 12:00 PM

```text
Okay I think no lock on is still better. lets revert to that. Lets also make sure that pinching doesnt shift the cursor. Right now when I pinch the cursor moves because my fingers move naturally while pinching, which means I click on something that I wasn't trying to click on. This combined with the removal of lock-on will make everything much more smooth and accurate
```

**Result (v0.4):** lock-on removed. The cursor now follows the knuckles, which don't move when you
pinch, so clicking no longer shifts it.

**Prompt 8** · Oct 4, 12:13 PM

```text
This might be hard to do, but similar to having the cursor stay still when I pinch (works great), it should also stay still when I twist a knob. Right now, when im twisting a knob the cursor moves somewhere else, causing issues
```

**Result (v0.5):** the cursor ignores the hand's rotation around the pinch point, and a 100 ms
release debounce stops a twist from flickering into a release.

**Prompt 9** · Oct 4, 4:33 PM

```text
one idea that I think would be good is to have hyrbrid lock on. I noticed even though on average no lock on is better, lock on for knobs that twist was actually better because thats how we keep the cursor in place while turning. However for buttons and sliders its better to not have lock on. While you are making these changes, also think about how to optimize the layout that would make the experience more smooth with the openCV hand control in mind. Also add like faster loops like 1/2 1/4 1/8 1/16 etc.
```

**Result (v0.6):**
- **Lock-on for knobs only.**
- **Rolls:** 1/2 to 1/16 of a beat, landing back in time when released.
- **A layout rework for hands:** bigger spacing, risky controls on the outer edges, rows in the
  order they're used.

**Prompt 10** · Oct 4, 5:12 PM

```text
I think the pinch distance is now a little too strict again, try to find a sweet spot. Also the turning is behaving weirdly now with the lock, make sure the turning is smooth when it locks on to the knob. After these changes it should be good for hand controls
```

**Result (v0.7):**
- **Smarter pinch:** a pinch now needs the thumb at the fingertip, not the joint.
- **Pinch sensitivity slider.**
- **Twist fixes:** no reset on quick re-grabs, and a fresh reference at each pinch.

### 4. More music, and automated transitions (Oct 4)

**Prompt 11** · Oct 4, 8:14 PM

```text
Can you download and preprocess some more tracks, look through audius and tell me what kind of genres there are
```

**Result:** 11 more real tracks across house, techno, trance, drum & bass, dubstep, and so on. Two
more analysis bugs found and fixed: a swelling kick put the grid 13 ms late, and rounded beat times
made synced decks drift.

**Prompt 12** · Oct 4, 9:15 PM

```text
Let's begin building chunk 6, the AI DJ
```

**Result (Chunk 6):**
- **Recipe format:** a contract for transitions as automation lanes.
- **Five preset transitions.**
- **An automation player** that schedules on the audio clock.
- **An echo effect.**
- **Handing back control:** grabbing a control takes it back from the automation.

The browser test confirmed every step lands on its bar.

### 5. Reworking the AI part: composing, not picking presets (Oct 5)

**Prompt 13** · Oct 5, 8:47 AM · the key design decision of the project

```text
Lets do chunk 7 now, but I want to completely rework chunk 7. Right now it seems like it's mostly just picking off preset transitions, which is fine but anyone can do that, I don't really need an AI element at all in this case. I want to rework it to the following. The AI composes transitions from building blocks: give it a vocabulary of moves (bass swap at bar N, a filter sweep, an echo throw, a stem drop, a loop roll, an EQ carve) plus the musical facts for both tracks: sections, energy curve, where vocals are, key and tempo. It writes a new recipe per pair, such as "drop A to drums on its breakdown, bring in B's vocal over it, echo-throw A's last phrase". Jev or a rules-based critic scores several candidates and the best one plays.
```

**Result (Chunk 7, rebuilt):**
- **The moves:** a vocabulary Claude writes plans in.
- **Facts per track:** sections, energy, vocals, key, tempo.
- **A compiler** that turns plans into automation recipes.
- **A rules-based critic** that simulates the mix bar by bar and scores it.
- **A rules composer** as the baseline.
- **The pipeline,** tested against recorded replies, with a cache so reruns are free.

**Prompt 14** · Oct 5, 9:41 AM

```text
Can we separate stems on just a few tracks right now
```

**Prompt 15** · Oct 5, 9:56 AM

```text
can we preprocess the rest of th library
```

**Result:** stems separated with Demucs, after fixing a level bug (stems came out 2 dB quiet) and a
DC-offset bug, both with tests.

**Prompt 16** · Oct 5, 1:37 PM · handing Codex's half-finished work to Claude Code

```text
I had codex implement the live LLM composer, where it can compose creative and sick transitions that extend beyond the very basics, given track A and track B. It should also be able to transition mid track, and not only at the end of a track. it ran out of usage limits halfway through, please check what has been done and what needs to be done and finish the job
```

**Result:** Claude Code audited Codex's composer (backend endpoint, Compose panel, mid-track exits,
two new moves), added an opt-in paid live test, fixed a pricing constant, and verified it with a
real composition starting mid-track.

### 6. Making it work across genres and keys (Oct 5)

**Prompt 17** · Oct 5, 4:15 PM

```text
The composer actually worked really well for two similar tracks in the same key. However it performs poorly when tracks are different genres and/or have different keys. Again I'm not a dj so I'm not too sure if thats just a given for human DJ's also, but maybe lets download some more tracks, and preprocess them for further testing. At the same time we can look into how to make cool transitions between genres and keys.
```

**Result:**
- **Diagnosis:** tracks that couldn't be synced were never allowed to overlap at all, and keys were
  judged as written, even though without key lock, syncing shifts pitch.
- **Fixes:**
  - stem presence per bar, so beatless and drums-only stretches are known;
  - keys judged as heard;
  - only overlapping *rhythms* counted as clashes;
  - two new moves, the tempo ride and the brake;
  - a playbook in the prompt.

**Prompt 18** · Oct 5, 5:31 PM

```text
I cant lie some of the tracks you picked are just straight bacteria music. like sweepah ambient is genuinely just ambient noise, discord drum&bass sounds like shit, humabatida also sounds like straight dogshit. Some of these dont work because the track itself just really suck. I do like the tracks with vocals in them. Overall I am very impressed by the improvement. Especially the prototype -> stars collide somehow works decently well now even though they are very different tracks.
```

**Prompt 19** · Oct 5, 5:33 PM

```text
Yes please, lets do a clean up and download/preprocess some good ones. Then we will be at a pretty good stage of the project
```

**Result:** the bad tracks removed. The downloader can now filter by popularity and length, and 8
well-played vocal tracks were added.

### 7. Making the composer good, not just valid (Oct 5–6)

**Prompt 20** · Oct 5, 8:49 PM

```text
Instead of composing 4 different transitions, can we just have it focus all its efforts on composing 1 really good transition. I want this one transition to be really good every time.
```

**Result:** three drafts, the strongest kept, then up to two revisions using the critic's findings
with bar numbers. One transition comes back.

**Prompt 21** · Oct 5, 9:18 PM · my ears vs the AI's critic

```text
From listening to it, I would say the composed one is actually better than the drum bridge
```

**Result:** Claude's compositions now rank above the rules baseline. The critic's weights were left
for calibration.

**Prompt 22** · Oct 5, 9:35 PM

```text
I think the AI composer is starting to get repetitive. I've noticed that it really likes doing very similar things over and over again, and there is no creativity. It functions almost as just a glorified rules that's just slightly more complicated. I'm not sure exactly what to change about this, but it would be cool if something was changed about this.
```

**Result:**
- **Concept cards:** each draft gets a different concept, from 16.
- **Memory:** a record of recent compositions, not to be repeated.
- **Novelty:** drafts that repeat recent work lose points.
- **A new "hook loop" move.**

### 8. Structure detection: drops, builds, choruses, hooks (Oct 6)

**Prompt 23** · Oct 6, 12:50 PM

```text
Lets improve the composition ability further. Let's add detection and labels for drops, builds, choruses, hooks, etc. I want these labels to be added to the tracks we have now, as well as the preprocessing pipeline. Then we can give the transition composer a lot more information, and we can tweak the prompt for it to then give us back way better transitions.
```

**Result (analysis format v4):**
- **Labels:** sections labelled from the stems; drop, build, breakdown and vocal cues; hooks.
- **Tested:** against the demo tracks' known structure.
- **Rules tuned on real tracks:** stepped builds, the peak threshold.
- **Shown on the board:** markers on the track overviews.
- **Used by the composer:** for example, landing B's drop exactly where A's build pays off.

### 9. Key lock and key shift (Oct 6)

**Prompt 24** · Oct 6, 1:57 PM

```text
lets now add key lock and key shifting. This will permanently fix key difference issues between tracks, and thus the composer will not have to rely on things like drum bridges to overcome that key difference.
```

**Result:**
- **A real-time pitch shifter** on each deck (Signalsmith Stretch, MIT licence), checked by
  measuring pitch in the actual audio.
- **A build fix:** a bundler problem in dev and production builds stopped it starting, and was
  fixed.
- **The composer can transpose B** by up to ±2 semitones.

**Prompt 25** · Oct 6, 2:48 PM · bug report after using it

```text
some small bugs to fix. the drop down keeps showing more and more duplicates of Echo Out (rules) every time I open it, I'm not too sure what's causing this. Everytime the name of the transition is too long, I can't see starts in (number) bars, it gets cut out. Sometimes when I'm composing for two tracks, it gives me a transition B into A instead of A into B. This is fine because for a real DJ it's whatever is playing now into whatever is queued with disregard of A or B, but there should be some way to specify that because I thought it was broken at first but it just turned out to be B into A instead of A into B which is what I assumed by default. Another issue with the key change is when I transitioned from A to B, B matched A's key, but transitioning back, A matched B's original key, so they swapped keys. When a track key matches another, it should just stay the same key. Other than that it works great.
```

**Result:**
- **The four bugs fixed:** key shifts are now relative to the key you hear; the rules baseline is
  replaced, not merged; a direction button; the countdown comes first.
- **One more found on the way:** an operator-precedence bug in the new direction code.

### 10. UI polish and quality of life (Oct 6)

**Prompt 26** · Oct 6, 4:36 PM

```text
alright lets really polish up the UI now without changing any functionalities
```

**Result:** a consistent top bar, clearer deck headers, and a fit fix at 1440×900, checked
geometrically at three screen sizes.

**Prompt 27** · Oct 6, 4:50 PM

```text
Some quality of life changes and bug fixes. The key lock snap to beat and master volume settings are static and cant be changed. I also want to make all knobs kinda biased towards the default setting, so it clicks into place or else itll be very hard to get the knobs back dead center, just like on a real DJ board. Lastly, the UI should work even if the window shrinks, which is not whats happening right now.
```

**Result:**
- **Top-bar controls fixed:** mouse input had only been wired to the board area.
- **Knob detents:** knobs click into their defaults.
- **Small windows:** the board scales as a whole, keeping controls where hands expect them.

### 11. Deployment (Oct 7)

These questions made the hosting decision before anything was built:

**Prompt 28** · Oct 7, 8:27 AM

```text
how do I host this on like vercel now
```

**Prompt 29** · Oct 7, 8:30 AM

```text
can we host it on render then or is it the same issues
```

**Prompt 30** · Oct 7, 8:33 AM

```text
so whats the most i can do with free render
```

**Prompt 31** · Oct 7, 8:36 AM

```text
whats wrong with having the audius tracks if Im only sharing the deployed link to course staff for a grade?
```

**Result:** the AI flagged the music licences: most tracks are "All rights reserved", and two were
unofficial edits of major-label songs. I decided on a private deployment, and those two tracks were
removed.

**Prompt 32** · Oct 7, 8:39 AM

```text
Okay let's do the access-code version, then give me detailed instructions on how to set up render and the R2 bucket such that someone with the deployed link can use handoff smoothly.
```

**Result:**
- **Access gate:** an access code.
- **Signed links:** a backend that hands out links to a private R2 bucket that expire after 6 hours.
- **Protection:** a rate limit, and CORS limited to the board's address.
- **Compositions kept in the browser.**
- **Deploy files:** `render.yaml` and an upload script.
- **Tested locally:** end to end, with a stand-in for R2.
- **A step-by-step guide:** `docs/DEPLOY.md`.

Debugging the live deployment:

**Prompt 33** · Oct 7, 12:37 PM

```text
I did all the steps correctly, and the webpage link works and I gave it the access code, but after a few minutes it says The server didn't start. Try again in a minute. how would i trouble shoot this
```

**Prompt 34** · Oct 7, 12:43 PM

```text
yup I found the error, the handoff-api URL did not show ok true, and also in f12 on the board im seeing Access to fetch at 'https://handoff-api.onrender.com/api/session' from origin 'https://handoff-web.onrender.com' has been blocked by CORS policy: Response to preflight request doesn't pass access control check: No 'Access-Control-Allow-Origin' header is present on the requested resource.
handoff-api.onrender.com/api/session:1  Failed to load resource: net::ERR_FAILED
```

**Prompt 35** · Oct 7, 12:46 PM

```text
it says {"detail":"Not Found"}
```

**Result:**
- **Cause:** the name `handoff-api` was already taken on Render, so `handoff-api.onrender.com` was
  someone else's server, and my board was calling it.
- **How it was found:** the AI checked the URL and saw a Node.js error format. My backend is
  Python, so the real one has a suffixed address.
- **The gate's message improved:** it now says when the address is the problem.

### 12. Documentation (Oct 7)

**Prompt 36** · Oct 7, 2:46 PM

```text
Please update the README with the following criteria. I know I'm supposed to write this myself, but what I want you to do is write generally what is supposed to be included and I will rewrite it all in my own words:
```

**Result:** a README scaffold of notes per required section, for me to rewrite in my own words.
The old AI-written README was moved under an "AI-generated documentation" heading.

---

## One place AI got it wrong

The AI's critic, which Claude Code designed to score transitions automatically, was confidently
wrong about what sounds good. It treated any two synced tracks more than 20 cents apart in pitch as
out of tune, and penalised them. So it marked down the same-key blends between zaza and EvenFall
that I'd just told it "worked really well", and preferred a plain echo out instead. Later it scored
a rules "drum bridge" 96 against Claude's composed transition at 89.5 for the same pair. Listening
to both, the composed one was clearly better (Prompt 21). I fed my listening back each time:
- **The detune threshold** was recalibrated to my ears: no penalty up to 40 cents.
- **The board** now ranks the AI's compositions above the rules baseline, rather than trusting the
  critic's numbers.

The lesson I took: an automated metric written by the same AI is only a hypothesis until a human
checks it against the real thing.

Other examples worth discussing:
- **A bug that slipped past the AI's tests:** "Echo out" duplicates kept piling up in the menu.
  Whenever the rules' format changed, their IDs changed, and the merge kept the old copies. All the
  tests passed, and I found it in normal use.
- **A design that couldn't work as written:** the first key-shift version swapped keys when
  transitioning back.
- **A misleading error message:** the deploy gate said "The server didn't start" when the real
  problem was a name collision on Render.
