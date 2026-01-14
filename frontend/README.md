# CyberPlat Frontend

Production-grade MVP frontend for CyberPlat SaaS platform.

## Features

- 📧 Inbox: Email ingestion status (queued/processing/done/failed/dead)
- 📄 Document upload and management
- 🔍 OCR processing on documents
- 📋 Invoice listing and details
- ✅ Invoice confirmation
- 📤 Invoice export (Excel/JSON)
- 💰 Billing invoice viewing

## Tech Stack

- **Next.js 14** (App Router)
- **TypeScript**
- **React 18**
- Minimal styling (no external UI library)

## Getting Started

### Prerequisites

- Node.js 18+ and npm

### Installation

```bash
# Install dependencies
npm install

# Run development server
npm run dev
```

The app will be available at `http://localhost:3000`.

### Build for Production

```bash
# Build
npm run build

# Start production server
npm start
```

## Configuration

### Settings Page

Before using the app, configure your settings:

1. Navigate to `/app/settings`
2. Enter your **Base URL** (e.g., `http://127.0.0.1:8000`)
3. Enter your **Tenant ID** (e.g., `tenant-1`)
4. Click "Save Settings"

Settings are stored in `localStorage` and persist across sessions.

### Default Values

- **Base URL**: `http://127.0.0.1:8000` (default backend URL)
- **Tenant ID**: Empty (must be configured)

## Typical Flow

### 0. Email Ingestion (Optional)

1. Send an email with PDF attachment to `invoices+your-tenant-id@yourapp.ai`
2. Go to `/app/inbox` to see the email status
3. Status will change: queued → processing → done
4. Click "Document" or "Invoice" links to view the created artifacts

### 1. Upload Document

1. Go to `/app/documents`
2. Click "Upload Document"
3. Select a PDF file
4. Document is uploaded and appears in the list

### 2. Run OCR

1. Click "View" on a document
2. Click "Run OCR" button
3. Wait for processing to complete
4. You'll be redirected to the invoice detail page

### 3. View Invoice

1. Go to `/app/invoices` to see all invoices
2. Click "View" on an invoice to see details
3. Review invoice fields and information

### 4. Confirm Invoice

1. On the invoice detail page
2. Click "Confirm Invoice"
3. Status changes to "confirmed"

### 5. Export Invoice

1. After confirming, click "Export Excel" or "Export JSON"
2. File is generated and automatically downloaded
3. Status changes to "exported"

### 6. View Billing

1. Go to `/app/billing`
2. Select a period (YYYY-MM format, e.g., `2026-01`)
3. View billing summary and totals by metric

## API Endpoints

The frontend communicates with the FastAPI backend using these endpoints:

- `GET /api/v1/inbox/emails` - List inbox emails (email ingestion status)
- `POST /documents/upload` - Upload document
- `GET /api/v1/documents` - List documents
- `GET /api/v1/documents/{id}` - Get document detail
- `POST /api/v1/documents/{id}/run-ocr` - Run OCR
- `GET /files/{file_id}` - Get file (for PDF viewer)
- `GET /api/v1/invoices` - List invoices
- `GET /api/v1/invoices/{id}` - Get invoice detail
- `POST /api/v1/invoices/{id}/confirm` - Confirm invoice
- `POST /api/v1/invoices/{id}/export` - Export invoice
- `GET /api/v1/billing/invoice?period=YYYY-MM` - Get billing invoice

All requests include the `X-Tenant-ID` header automatically.

## Project Structure

```
frontend/
├── app/                    # Next.js App Router pages
│   ├── settings/          # Settings page
│   ├── inbox/             # Inbox page (email ingestion status)
│   ├── documents/         # Documents list and detail
│   ├── invoices/          # Invoices list and detail
│   └── billing/           # Billing page
├── components/             # React components
│   ├── Nav.tsx            # Navigation bar
│   ├── ErrorBanner.tsx    # Error messages
│   └── SuccessBanner.tsx   # Success messages
├── lib/                    # Utilities
│   ├── api.ts             # API client
│   └── storage.ts         # localStorage utilities
└── README.md              # This file
```

## Development

### Type Checking

```bash
npm run build
```

### Linting

```bash
npm run lint
```

## Troubleshooting

### "Tenant ID is not set"

- Go to `/app/settings` and configure your Tenant ID

### "Failed to load documents"

- Check that the backend is running at the configured Base URL
- Verify the Tenant ID is correct
- Check browser console for detailed error messages

### CORS Errors

- Ensure the backend allows requests from `http://localhost:3000`
- Check backend CORS configuration

## Notes

- This is an MVP version with minimal styling
- No authentication is implemented (tenant ID is stored in localStorage)
- File uploads are handled via multipart form-data
- PDF viewing uses iframe with backend file endpoint
- Export files are automatically downloaded
