# QuizArena (Django + Gemini) - v4

Two things in one project:

1. **AI practice quizzes** (the original app): upload notes, Gemini writes the quiz, students compete.
2. **Live exams** (new `exams` app): a teacher section where teachers create a quiz, start it, share a
   test code and watch students in real time, with tab-switch warnings and per-student control.

## Setup (Windows commands; Linux/Mac in brackets)

1. Install Python 3.10+ and open a terminal inside this folder (the one with manage.py).
2. Create a virtual environment:  python -m venv venv
3. Activate it:  venv\Scripts\activate   (Linux/Mac: source venv/bin/activate)
4. Install packages:  pip install -r requirements.txt
5. Set your Gemini key (only needed for the AI practice quizzes):
       set GEMINI_API_KEY=your_key_here        (Linux/Mac: export GEMINI_API_KEY=your_key_here)
6. Optional but recommended: choose your own teacher invite code
       set TEACHER_INVITE_CODE=my-secret-code  (default is teacher123)
7. Create the database tables:
       python manage.py makemigrations quiz exams
       python manage.py migrate
8. Run:  python manage.py runserver
9. Open http://127.0.0.1:8000/

## Using live exams

Teacher
- Go to **Teacher Login** (top menu) > "Create a teacher account". You need the invite code (step 6).
- Dashboard > **New Quiz**: title, description, duration, optional start time and deadline, questions
  (4 options, correct answer, marks), test-code settings, warning rules.
- Open the quiz > **Start quiz now**. A 6-character test code appears. Tell it to your students.
- The live dashboard shows every student (status, warnings, answered, time left) and notifications.
  Each student has a Terminate / Remove button. **Close quiz for everyone** ends the whole quiz.
- You can edit or delete a quiz only while it is still a draft.

Student
- Register / login as usual, open **Join Exam**, enter name, roll number and the test code.
- Read the rules, tick the box, press Start. Answers autosave. Submit at the end.

## How the rules work
- **The server keeps the time.** Start, end, each student's remaining time and the deadline are decided
  by the server. The countdown in the browser is only a display, and every save or heartbeat re-syncs it.
  After time is up (or the teacher closes the quiz) the server refuses new answers.
- **Test code:** created when the quiz starts. It can expire after N minutes. When it expires you choose:
  create a new code automatically, or stop new students from joining. The teacher can also regenerate
  or disable the code at any time. Students who already joined are never affected by code changes.
- **Per-student timer:** each student gets the full duration from their own Start, but never beyond the
  quiz end (the deadline, or one duration after the quiz started if no deadline is set).
- **Terminate:** stops one student immediately, keeps their saved answers and warning history, shows them
  the reason. Other students are not affected.

## Important: what tab-switch detection can and cannot do
The browser tells the page when it becomes hidden (tab switch, minimized window, locked screen). That is
all it knows. It CANNOT see other applications, other devices, a phone, split-screen use or a second
person helping. A warning is a signal for the teacher, not proof of cheating.

## Real-time notifications
The teacher dashboard asks the server for updates every 3 seconds (simple "polling", no extra software
needed). An alert can therefore arrive up to about 3 seconds after it happens. For hundreds of students
at once use a stronger database than SQLite (for example PostgreSQL).

## Changing the Gemini model
Default is gemini-3.8-flash (quizproject/settings.py). Override without editing code:
       set GEMINI_MODEL=new_model_name

## Restart a student's quiz (v4.1)
- Open a live quiz > in the Students table press **Restart Quiz** next to a student (also on the student's detail page).
  A confirmation box appears first. Works while the quiz session is still running.
- That one student gets a fresh attempt (answers, warnings, flag and timer reset; full time, never beyond the quiz end).
  Their old attempt (marks, answers, warnings, who restarted it and when) is stored in **Attempt history** on their detail page.
  Other students are not touched. The student sees a "Your quiz was restarted" message and a banner on the rules page.
- Only the student's own login can open the restarted attempt.

## Reconduct a test (v4.1)
- Teacher Dashboard > **Reconduct Test** (or **Reconduct** on a quiz card). Questions, options, marks and settings are copied
  into a NEW session; change date, time, duration or any setting. A fresh test code is created when the session starts.
- Each session is a separate record with its own students, results, code and monitor page. The old session is never changed.
- Sessions of the same test are linked at the top of the monitor page.

## After updating the code
    python manage.py makemigrations exams
    python manage.py migrate

---

## What is new in v5

- **Generate questions with Gemini** inside the teacher quiz editor (upload notes, review, edit, save).
- **Shuffle** questions and options for each student (marking never depends on the order shown).
- **Fullscreen**: students must stay in fullscreen; leaving it counts as a warning.
- **Copy / cut / paste / right-click blocked** on the quiz page.
- **Pause and resume** the whole quiz or one student. Timers freeze and nobody loses time.
- **Extra time** for everyone taking the quiz or for one student.
- **Student dashboard** ("My Exams"): past exams, scores, progress, practice quizzes.

Each of the first four can be switched on or off per quiz (Exam environment section of the quiz form).

### Upgrading from v4
1. Stop the server. Do it when no exam is running.
2. Copy the new files over your project (or unzip into a new folder for a fresh database).
3. `python manage.py makemigrations exams` then `python manage.py migrate`.
4. Existing quizzes get the new settings switched ON by default. Edit a draft quiz to change them.

### How pause and extra time work
- The server keeps every deadline. Pausing never costs a student time: when the quiz is resumed, the
  closing time moves later by the length of the pause and each student's time is restored.
- While paused, students cannot answer and nothing counts as a warning (tab switch, leaving fullscreen).
- A single student can be paused or resumed on their own row. While the whole quiz is paused, single
  pauses and resumes are disabled (resume the whole quiz first).
- "Add time for everyone" gives the minutes to every student who is taking the quiz right now and moves
  the closing time later. Students who have not started yet are not affected.
- "Add time" on one row gives only that student more time, even past the closing time. The quiz closes
  by itself only after every student who still has time has finished. After the closing time, nobody new
  can join or start.

### Limits you should know about
- Fullscreen, copy/paste blocking and tab detection all run in the student's browser. A determined student
  can get around them (another device, a photo of the screen, developer tools). Use them to discourage
  cheating, not to prove it.
- A browser only allows fullscreen after a click, so the student clicks "Enter fullscreen" when the quiz
  opens (the timer is already running). Some phones and tablets (for example iPhone) cannot do fullscreen;
  there the rule is skipped automatically.
- AI-generated questions can be wrong. Always read every question and answer before saving.
