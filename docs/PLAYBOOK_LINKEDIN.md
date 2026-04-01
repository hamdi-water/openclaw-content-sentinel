# Playbook: LinkedIn Publication

## Protocol §35.1

1. **Authentication**: Ensure the browser profile `linkedin` is authenticated.
2. **Navigation**: Navigate to `https://www.linkedin.com/feed/`.
3. **Draft Injection**: 
   - Locate the "Start a post" button.
   - Inject the generated LinkedIn draft from `drafts/linkedin.md`.
4. **Media Attachment**:
   - Click the "Add media" icon.
   - Upload `media/social_card.png`.
5. **Quality Check**:
   - Verify the image preview aligns with the text.
   - Check for PII or non-compliant keywords.
6. **Submission**: Click "Post".
7. **Verification**: 
   - Wait for the "Post successful" toast.
   - Capture a screenshot of the live post for the ledger.

## Fallback Mode
If automated selectors fail, degrade to manual copy-paste via the Telegram cockpit.
