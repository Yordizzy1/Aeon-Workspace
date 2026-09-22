from database import consume_credit, get_purchase_status, get_usage_summary


class InvalidApiKey(RuntimeError):
    pass


class InsufficientCredits(RuntimeError):
    pass


class EventIdConflict(RuntimeError):
    pass


class BillingAdapter:
    InvalidApiKey = InvalidApiKey
    InsufficientCredits = InsufficientCredits
    EventIdConflict = EventIdConflict

    def consume(self, *, api_key, event_id=None):
        result = consume_credit(api_key, event_id)
        status = result.get("status")

        if status == "invalid_api_key":
            raise InvalidApiKey()
        if status == "insufficient_credits":
            raise InsufficientCredits()
        if status == "event_id_conflict":
            raise EventIdConflict()

        return result

    def usage(self, api_key):
        result = get_usage_summary(api_key)
        if result.get("status") == "invalid_api_key":
            raise InvalidApiKey()
        return result

    def purchase_status(self, session_id):
        return get_purchase_status(session_id)
