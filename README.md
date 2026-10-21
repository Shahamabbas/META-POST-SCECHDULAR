# META-POST-SCECHDULAR

> **Social Studio — Meta Post Scheduler**  
> A complete, locally runnable single-owner scheduling application for Meta (Facebook & Instagram) with zero external pip/npm dependencies.

For full setup instructions, architecture details, and Meta integration guidelines, refer to [`meta-scheduler/README.md`](meta-scheduler/README.md).

---

## Quick Start

Navigate to the `meta-scheduler` directory:

### Windows PowerShell
```powershell
cd meta-scheduler
$env:APP_PASSWORD = Read-Host 'Choose a workspace password (12+ characters)'
python app.py
```

### macOS / Linux (Bash)
```bash
cd meta-scheduler
read -rsp 'Choose a workspace password (12+ characters): ' APP_PASSWORD
export APP_PASSWORD
python3 app.py
```

Open **http://127.0.0.1:8080** in your browser and sign in with your chosen password.

---

## Features

- **Zero Third-Party Dependencies**: Pure Python standard library (3.11+) + vanilla JS & modern CSS.
- **Security First**: PBKDF2-HMAC-SHA256 password hashing, HttpOnly session cookies, CSRF protection, and rate limiting.
- **Account Management**: Connect up to three Facebook Page or Instagram destinations.
- **Rich Post Creation**: Text captions, single JPEG/PNG/MP4 media uploads, live desktop and mobile preview.
- **Flexible Scheduling**: Timezone-aware scheduling with strict daylight-saving transition validation.
- **Content Pipeline**: Full draft, scheduled, publishing, and activity history tracking.
