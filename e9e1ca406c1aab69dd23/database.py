import hashlib
import os
import secrets
import uuid

import psycopg
from psycopg.rows import dict_row


class DatabaseError(RuntimeError):
    pass


class PurchaseNotReady(RuntimeError):
    pass


class PurchaseAlreadyClaimed(RuntimeError):
    pass


def _database_url():
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise DatabaseError("DATABASE_URL is not configured")
    return url


def _connect():
    try:
        return psycopg.connect(_database_url(), row_factory=dict_row)
    except Exception as exc:
        raise DatabaseError(str(exc)) from exc


def _hash_api_key(api_key):
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def init_db():
    statements = [
        """
        CREATE TABLE IF NOT EXISTS customers (
            id BIGSERIAL PRIMARY KEY,
            email TEXT,
            livemode BOOLEAN NOT NULL DEFAULT FALSE,
            credit_balance BIGINT NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (email, livemode)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS api_keys (
            id BIGSERIAL PRIMARY KEY,
            customer_id BIGINT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
            key_hash TEXT UNIQUE NOT NULL,
            key_prefix TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            revoked_at TIMESTAMPTZ
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS stripe_events (
            event_id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            received_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS purchases (
            id BIGSERIAL PRIMARY KEY,
            stripe_session_id TEXT UNIQUE NOT NULL,
            email TEXT,
            amount_total BIGINT NOT NULL DEFAULT 0,
            currency TEXT NOT NULL DEFAULT 'usd',
            payment_status TEXT NOT NULL,
            credits BIGINT NOT NULL DEFAULT 0,
            livemode BOOLEAN NOT NULL DEFAULT FALSE,
            claimed BOOLEAN NOT NULL DEFAULT FALSE,
            customer_id BIGINT REFERENCES customers(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS credit_ledger (
            id BIGSERIAL PRIMARY KEY,
            customer_id BIGINT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
            delta BIGINT NOT NULL,
            reason TEXT NOT NULL,
            reference TEXT UNIQUE NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS usage_events (
            event_id TEXT PRIMARY KEY,
            customer_id BIGINT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
            api_key_id BIGINT NOT NULL REFERENCES api_keys(id) ON DELETE CASCADE,
            credits_charged BIGINT NOT NULL DEFAULT 1,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ]

    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                for statement in statements:
                    cur.execute(statement)
            conn.commit()
    except Exception as exc:
        if isinstance(exc, DatabaseError):
            raise
        raise DatabaseError(str(exc)) from exc


def db_health():
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 AS ok")
                row = cur.fetchone()
        return bool(row and row["ok"] == 1), "ok"
    except Exception as exc:
        return False, str(exc)


def process_stripe_event(*, event_id, event_type, livemode, session_id, email,
                         amount_total, currency, payment_status, credits):
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO stripe_events (event_id, event_type)
                    VALUES (%s, %s)
                    ON CONFLICT (event_id) DO NOTHING
                    RETURNING event_id
                    """,
                    (event_id, event_type),
                )
                if cur.fetchone() is None:
                    conn.commit()
                    return {"duplicate_event": True}

                cur.execute(
                    """
                    INSERT INTO purchases (
                        stripe_session_id, email, amount_total, currency,
                        payment_status, credits, livemode
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (stripe_session_id)
                    DO UPDATE SET
                        email = COALESCE(EXCLUDED.email, purchases.email),
                        amount_total = GREATEST(purchases.amount_total, EXCLUDED.amount_total),
                        currency = EXCLUDED.currency,
                        payment_status = EXCLUDED.payment_status,
                        credits = GREATEST(purchases.credits, EXCLUDED.credits),
                        livemode = EXCLUDED.livemode,
                        updated_at = NOW()
                    RETURNING stripe_session_id, payment_status, credits, claimed
                    """,
                    (session_id, email, amount_total, currency,
                     payment_status, credits, livemode),
                )
                purchase = cur.fetchone()
            conn.commit()
        return {"duplicate_event": False, "purchase": purchase}
    except Exception as exc:
        if isinstance(exc, DatabaseError):
            raise
        raise DatabaseError(str(exc)) from exc


def get_purchase_status(session_id):
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT stripe_session_id, email, payment_status, credits,
                           livemode, claimed
                    FROM purchases
                    WHERE stripe_session_id = %s
                    """,
                    (session_id,),
                )
                return cur.fetchone()
    except Exception as exc:
        if isinstance(exc, DatabaseError):
            raise
        raise DatabaseError(str(exc)) from exc


def _get_or_create_customer(cur, email, livemode):
    normalized_email = (
        email.strip().lower()
        if isinstance(email, str) and email.strip()
        else None
    )

    if normalized_email:
        cur.execute(
            """
            INSERT INTO customers (email, livemode)
            VALUES (%s, %s)
            ON CONFLICT (email, livemode)
            DO UPDATE SET email = EXCLUDED.email
            RETURNING id, email, livemode, credit_balance
            """,
            (normalized_email, livemode),
        )
        return cur.fetchone()

    cur.execute(
        """
        INSERT INTO customers (email, livemode)
        VALUES (NULL, %s)
        RETURNING id, email, livemode, credit_balance
        """,
        (livemode,)
    )
    return cur.fetchone()


def claim_purchase(session_id):
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM purchases WHERE stripe_session_id = %s FOR UPDATE",
                    (session_id,),
                )
                purchase = cur.fetchone()

                if not purchase or purchase["payment_status"] != "paid" or int(purchase["credits"]) <= 0:
                    raise PurchaseNotReady()
                if purchase["claimed"]:
                    raise PurchaseAlreadyClaimed()

                customer = _get_or_create_customer(cur, purchase["email"], purchase["livemode"])
                reference = "purchase:" + purchase["stripe_session_id"]

                cur.execute(
                    """
                    INSERT INTO credit_ledger (customer_id, delta, reason, reference)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (reference) DO NOTHING
                    RETURNING id
                    """,
                    (customer["id"], int(purchase["credits"]), "stripe_purchase", reference),
                )

                if cur.fetchone() is not None:
                    cur.execute(
                        "UPDATE customers SET credit_balance = credit_balance + %s WHERE id = %s",
                        (int(purchase["credits"]), customer["id"]),
                    )

                raw_api_key = (
                    ("aeon_live_" if purchase["livemode"] else "aeon_test_")
                    + secrets.token_urlsafe(32)
                )
                key_hash = _hash_api_key(raw_api_key)
                key_prefix = raw_api_key[:18]

                cur.execute(
                    """
                    INSERT INTO api_keys (customer_id, key_hash, key_prefix)
                    VALUES (%s, %s, %s)
                    """,
                    (customer["id"], key_hash, key_prefix),
                )

                cur.execute(
                    """
                    UPDATE purchases
                    SET claimed = TRUE, customer_id = %s, updated_at = NOW()
                    WHERE id = %s
                    """,
                    (customer["id"], purchase["id"]),
                )

                cur.execute(
                    "SELECT credit_balance FROM customers WHERE id = %s",
                    (customer["id"],),
                )
                balance = int(cur.fetchone()["credit_balance"])
            conn.commit()

        return {
            "api_key": raw_api_key,
            "api_key_prefix": key_prefix,
            "email": purchase["email"],
            "credits_remaining": balance,
        }
    except (PurchaseNotReady, PurchaseAlreadyClaimed):
        raise
    except Exception as exc:
        if isinstance(exc, DatabaseError):
            raise
        raise DatabaseError(str(exc)) from exc


def _authenticate_key(cur, api_key):
    cur.execute(
        """
        SELECT api_keys.id AS api_key_id, api_keys.customer_id,
               api_keys.key_prefix, customers.email, customers.credit_balance
        FROM api_keys
        JOIN customers ON customers.id = api_keys.customer_id
        WHERE api_keys.key_hash = %s AND api_keys.revoked_at IS NULL
        """,
        (_hash_api_key(api_key),),
    )
    return cur.fetchone()


def consume_credit(api_key, event_id=None):
    event_id = (
        event_id.strip()
        if isinstance(event_id, str) and event_id.strip()
        else "evt_" + uuid.uuid4().hex
    )

    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                auth = _authenticate_key(cur, api_key)
                if not auth:
                    return {"status": "invalid_api_key"}

                cur.execute(
                    "SELECT event_id, customer_id, credits_charged FROM usage_events WHERE event_id = %s",
                    (event_id,),
                )
                existing = cur.fetchone()
                if existing:
                    if existing["customer_id"] != auth["customer_id"]:
                        return {"status": "event_id_conflict"}
                    cur.execute(
                        "SELECT credit_balance FROM customers WHERE id = %s",
                        (auth["customer_id"],),
                    )
                    current = int(cur.fetchone()["credit_balance"])
                    return {
                        "status": "ok",
                        "event_id": event_id,
                        "credits_charged": int(existing["credits_charged"]),
                        "credits_remaining": current,
                        "idempotent_replay": True,
                    }

                cur.execute(
                    "SELECT credit_balance FROM customers WHERE id = %s FOR UPDATE",
                    (auth["customer_id"],),
                )
                balance = int(cur.fetchone()["credit_balance"])
                if balance < 1:
                    conn.rollback()
                    return {"status": "insufficient_credits"}

                cur.execute(
                    "UPDATE customers SET credit_balance = credit_balance - 1 WHERE id = %s",
                    (auth["customer_id"],),
                )
                cur.execute(
                    """
                    INSERT INTO usage_events (event_id, customer_id, api_key_id, credits_charged)
                    VALUES (%s, %s, %s, 1)
                    """,
                    (event_id, auth["customer_id"], auth["api_key_id"]),
                )
                cur.execute(
                    """
                    INSERT INTO credit_ledger (customer_id, delta, reason, reference)
                    VALUES (%s, -1, %s, %s)
                    """,
                    (auth["customer_id"], "api_usage", "usage:" + event_id),
                )
                cur.execute(
                    "SELECT credit_balance FROM customers WHERE id = %s",
                    (auth["customer_id"],),
                )
                remaining = int(cur.fetchone()["credit_balance"])
            conn.commit()

        return {
            "status": "ok",
            "event_id": event_id,
            "credits_charged": 1,
            "credits_remaining": remaining,
            "idempotent_replay": False,
        }
    except Exception as exc:
        if isinstance(exc, DatabaseError):
            raise
        raise DatabaseError(str(exc)) from exc


def get_usage_summary(api_key):
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                auth = _authenticate_key(cur, api_key)
                if not auth:
                    return {"status": "invalid_api_key"}

                cur.execute(
                    "SELECT COUNT(*) AS total_calls FROM usage_events WHERE customer_id = %s",
                    (auth["customer_id"],),
                )
                total_calls = int(cur.fetchone()["total_calls"])

                return {
                    "status": "ok",
                    "email": auth["email"],
                    "api_key_prefix": auth["key_prefix"],
                    "credits_remaining": int(auth["credit_balance"]),
                    "total_calls": total_calls,
                }
    except Exception as exc:
        if isinstance(exc, DatabaseError):
            raise
        raise DatabaseError(str(exc)) from exc



def get_public_business_metrics():
    """Return aggregate billing telemetry without PII or credential material."""

    def _mode_metrics(cur, livemode):
        cur.execute(
            """
            SELECT
                COUNT(*) AS paid_checkouts,
                COUNT(
                    DISTINCT COALESCE(
                        NULLIF(LOWER(email), ''),
                        stripe_session_id
                    )
                ) AS paid_customers,
                COALESCE(SUM(amount_total), 0) AS paid_cents,
                COALESCE(SUM(credits), 0) AS credits_sold,
                COALESCE(
                    SUM(CASE WHEN claimed THEN 1 ELSE 0 END),
                    0
                ) AS claimed_purchases,
                MAX(updated_at) AS last_payment_at
            FROM purchases
            WHERE livemode = %s
              AND payment_status = 'paid'
            """,
            (livemode,),
        )
        purchases = cur.fetchone()

        cur.execute(
            """
            SELECT amount_total
            FROM purchases
            WHERE livemode = %s
              AND payment_status = 'paid'
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            (livemode,),
        )
        last_purchase = cur.fetchone()

        cur.execute(
            """
            SELECT
                COUNT(*) AS customer_records,
                COALESCE(SUM(credit_balance), 0) AS credits_remaining
            FROM customers
            WHERE livemode = %s
            """,
            (livemode,),
        )
        customers = cur.fetchone()

        cur.execute(
            """
            SELECT COUNT(*) AS api_calls
            FROM usage_events AS usage
            JOIN customers
              ON customers.id = usage.customer_id
            WHERE customers.livemode = %s
            """,
            (livemode,),
        )
        usage = cur.fetchone()

        cur.execute(
            """
            SELECT COUNT(*) AS active_api_keys
            FROM api_keys
            JOIN customers
              ON customers.id = api_keys.customer_id
            WHERE customers.livemode = %s
              AND api_keys.revoked_at IS NULL
            """,
            (livemode,),
        )
        keys = cur.fetchone()

        paid_cents = int(purchases['paid_cents'] or 0)
        last_payment_at = purchases['last_payment_at']

        return {
            'paid_checkouts': int(purchases['paid_checkouts'] or 0),
            'paid_customers': int(purchases['paid_customers'] or 0),
            'gross_paid_cents': paid_cents,
            'gross_paid_usd': round(paid_cents / 100.0, 2),
            'credits_sold': int(purchases['credits_sold'] or 0),
            'claimed_purchases': int(purchases['claimed_purchases'] or 0),
            'customer_records': int(customers['customer_records'] or 0),
            'credits_remaining': int(customers['credits_remaining'] or 0),
            'api_calls_consumed': int(usage['api_calls'] or 0),
            'active_api_keys': int(keys['active_api_keys'] or 0),
            'last_payment_at': (
                last_payment_at.isoformat()
                if last_payment_at is not None
                else None
            ),
            'last_payment_usd': (
                round(int(last_purchase['amount_total']) / 100.0, 2)
                if last_purchase is not None
                else None
            ),
        }

    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                live = _mode_metrics(cur, True)
                test = _mode_metrics(cur, False)
        return {'live': live, 'test': test}
    except Exception as exc:
        if isinstance(exc, DatabaseError):
            raise
        raise DatabaseError(str(exc)) from exc
