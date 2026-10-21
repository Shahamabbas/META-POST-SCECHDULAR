"""The only Meta-specific publishing boundary. No Meta SDK or credentials included."""
class IntegrationRequired(Exception):
    """No request was sent. Retry is safe after configuring the adapter."""

class PublishRejected(Exception):
    """Use ONLY when the API definitively confirms no post was created."""

class Publisher:
    def publish(self, *, account: dict, caption: str, media: dict | None, idempotency_key: str) -> str:
        """Return a confirmed remote post ID, never a made-up success value.

        account: id, name, platform ('facebook'/'instagram'), external_id.
        media: None or {id,name,mime,size,path}; path is a local absolute path.
        idempotency_key: stable delivery ID, unchanged across manual retries.

        Add your Meta code here:
        1. Resolve account credentials server-side from your own secret store.
        2. Validate platform media dimensions, codecs, duration and size.
        3. Upload/host media as required by your chosen publishing API.
        4. Persist container/request IDs in your integration storage for recovery.
        5. Reconcile an existing request using idempotency_key before resending.
        6. Publish and return the confirmed platform post ID.

        Raise IntegrationRequired before sending when configuration is missing.
        Raise PublishRejected for definite rejection; all other exceptions become
        'unknown' and require human verification before any retry.
        The scheduler intentionally does not retry uncertain remote side effects.
        """
        raise IntegrationRequired('Meta integration is not configured. Implement Publisher.publish in integration.py, then retry this delivery.')
