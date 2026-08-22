-- RecoverAI — seed data for local / Supabase testing
-- Safe to re-run: truncates existing rows first.

TRUNCATE TABLE audit_logs, recovery_cases, payments, customers RESTART IDENTITY CASCADE;

-- ---------------------------------------------------------------------------
-- 20 customers
-- High-value: Priya, Arjun, Nisha, Vikram, Ananya
-- Mid-value:  Rohan, Meera, Kabir, Sneha, Aditya, Fatima, Harsh
-- Low-value:  Diya, Kunal, Aisha, Ravi, Pooja, Sameer, Tanya, Nikhil
-- ---------------------------------------------------------------------------
INSERT INTO customers (id, name, email, phone, created_at) VALUES
    ('5980614a-0ceb-4c26-84cf-08a688a135f2', 'Priya Mehta',     'priya.mehta@lumenlabs.in',       '+919876543210', '2025-11-04 08:12:00+00'),
    ('ad0e0a38-e907-46b3-b890-e0b5cbdff634', 'Arjun Kapoor',    'arjun.kapoor@northstar.agency',  '+919820011122', '2025-12-18 11:40:00+00'),
    ('a8d8532d-9b88-467f-ba4b-a4f421888024', 'Nisha Reddy',     'nisha@bloomkart.com',            '+919848012345', '2025-10-09 06:05:00+00'),
    ('500053a7-2333-4ded-a65e-752bc23b2fc7', 'Vikram Shah',     'vikram.shah@careplus.clinic',    '+919090901234', '2026-01-14 10:22:00+00'),
    ('15366978-a54d-4079-b647-f33280d6d482', 'Ananya Iyer',     'ananya@learnloop.edu',           '+919884012233', '2026-02-02 07:48:00+00'),
    ('b8f2be42-08c6-4252-8ca5-9498b35fe6cf', 'Rohan Desai',     'rohan.desai@pixelcraft.io',      '+919811122233', '2026-01-28 13:15:00+00'),
    ('327df75a-3b56-4a3d-bf83-44b91ee6683f', 'Meera Joshi',     'meera.joshi@homebrew.cafe',      '+919765432109', '2026-03-11 09:01:00+00'),
    ('a6312223-2465-4b1d-a6b1-c8598cfb86f8', 'Kabir Khan',      'kabir.khan@fitbox.app',          '+919999988877', '2026-02-20 16:30:00+00'),
    ('927ff09b-1e55-4f7b-8cc3-16a5e29d1960', 'Sneha Nair',      'sneha.nair@ayurora.in',          '+919447012345', '2026-01-07 05:55:00+00'),
    ('6bfb8c60-fbb7-4a5c-a02d-f3986feb1fe3', 'Aditya Rao',      'aditya.rao@stackmint.dev',       '+919008012345', '2026-03-03 12:10:00+00'),
    ('137754ca-5217-47fc-a8c7-7a11bf678139', 'Fatima Ali',      'fatima.ali@zaytunfoods.com',     '+919823456789', '2026-02-14 08:44:00+00'),
    ('e2adae4d-9298-48af-9e4e-c4cc184f046d', 'Harsh Patel',     'harsh.patel@quickbill.in',       '+919876500011', '2026-01-21 14:20:00+00'),
    ('b04cd446-f930-4265-9060-2008ca9200b5', 'Diya Sharma',     'diya.sharma@gmail.com',          '+919810010001', '2026-04-16 18:02:00+00'),
    ('04643ea6-6f43-4f03-ab1e-7d731a6ef8c9', 'Kunal Verma',     'kunal.verma@yahoo.com',          '+919820020002', '2026-05-02 11:11:00+00'),
    ('545d0e1d-96ab-4c9b-a697-03b156c97bc5', 'Aisha Khan',      'aisha.k@outlook.com',            '+919830030003', '2026-03-22 09:33:00+00'),
    ('6577d41e-14f3-4324-961a-1ca3ac35cfff', 'Ravi Menon',      'ravi.menon@gmail.com',           '+919840040004', '2026-05-19 07:27:00+00'),
    ('e82604f8-8248-49b8-898d-cd1c4b087048', 'Pooja Gupta',     'pooja.gupta@gmail.com',          '+919850050005', '2026-04-08 15:50:00+00'),
    ('63d10308-d781-4bd4-9940-28ed15b1ad7a', 'Sameer Iqbal',    'sameer.iqbal@hotmail.com',       '+919860060006', '2026-02-27 10:05:00+00'),
    ('8b46c86f-5136-4cc4-b41a-26a0de54df7d', 'Tanya Bose',      'tanya.bose@gmail.com',           '+919870070007', '2026-06-01 19:18:00+00'),
    ('630cf89f-8620-4937-8aaa-e57b98ce3078', 'Nikhil Jain',     'nikhil.jain@gmail.com',          '+919880080008', '2026-03-30 06:40:00+00');

-- ---------------------------------------------------------------------------
-- 50 payments (INR)
-- Mix: succeeded, failed, and one pending checkout.
-- Failed reasons: insufficient_funds, bank_error, expired_card,
--                 authentication_failed, checkout_abandoned
-- ---------------------------------------------------------------------------
INSERT INTO payments (id, customer_id, amount, currency, status, failure_reason, paid_at, created_at) VALUES
    -- Priya Mehta (high) — 3 successful monthly invoices, then expired card
    ('d9445e98-0951-43f6-9955-ca7d518b9602', '5980614a-0ceb-4c26-84cf-08a688a135f2', 49999.00, 'INR', 'succeeded', NULL,                    '2026-04-12 09:02:11+00', '2026-04-12 09:01:40+00'),
    ('e159c081-23bb-4ac3-9f91-a83907255ab1', '5980614a-0ceb-4c26-84cf-08a688a135f2', 49999.00, 'INR', 'succeeded', NULL,                    '2026-05-12 09:04:02+00', '2026-05-12 09:03:21+00'),
    ('4c88c7cb-4c8f-4842-b8f4-ec81496b5a61', '5980614a-0ceb-4c26-84cf-08a688a135f2', 49999.00, 'INR', 'succeeded', NULL,                    '2026-06-12 09:01:55+00', '2026-06-12 09:01:12+00'),
    ('7f97ad41-1c29-4dfb-8cd6-d6712bb45143', '5980614a-0ceb-4c26-84cf-08a688a135f2', 49999.00, 'INR', 'failed',    'expired_card',          NULL,                     '2026-07-12 09:02:08+00'),

    -- Arjun Kapoor (high) — reliable payer, latest NSF
    ('e504157c-f0b8-4e58-9b15-65445fe8836a', 'ad0e0a38-e907-46b3-b890-e0b5cbdff634', 24999.00, 'INR', 'succeeded', NULL,                    '2026-05-03 14:22:40+00', '2026-05-03 14:22:01+00'),
    ('d1a31828-8a5e-45bc-958e-46c10a67810d', 'ad0e0a38-e907-46b3-b890-e0b5cbdff634', 24999.00, 'INR', 'succeeded', NULL,                    '2026-06-03 14:18:11+00', '2026-06-03 14:17:44+00'),
    ('f2dd2e82-724f-4cfa-b1bf-461aa6bd013c', 'ad0e0a38-e907-46b3-b890-e0b5cbdff634', 24999.00, 'INR', 'failed',    'insufficient_funds',    NULL,                     '2026-08-03 14:19:33+00'),

    -- Nisha Reddy (high) — large D2C payouts, bank error on latest
    ('a09c51fa-1633-4665-b652-c36bf2366864', 'a8d8532d-9b88-467f-ba4b-a4f421888024', 89999.00, 'INR', 'succeeded', NULL,                    '2026-03-21 06:40:18+00', '2026-03-21 06:39:50+00'),
    ('579db300-d22a-4b6d-aa1f-d2d87fc1ede3', 'a8d8532d-9b88-467f-ba4b-a4f421888024', 89999.00, 'INR', 'succeeded', NULL,                    '2026-06-21 06:41:02+00', '2026-06-21 06:40:29+00'),
    ('cac0b71f-61ae-41c1-9d93-73444ee32a15', 'a8d8532d-9b88-467f-ba4b-a4f421888024', 89999.00, 'INR', 'failed',    'bank_error',            NULL,                     '2026-08-18 06:42:15+00'),

    -- Vikram Shah (high) — clinic billing, 3DS / auth failure
    ('1d155051-d39e-4154-9bf2-7f37496ba432', '500053a7-2333-4ded-a65e-752bc23b2fc7', 34999.00, 'INR', 'succeeded', NULL,                    '2026-04-08 11:05:44+00', '2026-04-08 11:05:02+00'),
    ('29e0b695-0221-4460-a9db-e9a26314d5b1', '500053a7-2333-4ded-a65e-752bc23b2fc7', 34999.00, 'INR', 'succeeded', NULL,                    '2026-06-08 11:07:19+00', '2026-06-08 11:06:51+00'),
    ('0b2989a5-f12a-40c4-9ba3-9d83e3c413c9', '500053a7-2333-4ded-a65e-752bc23b2fc7', 34999.00, 'INR', 'failed',    'authentication_failed', NULL,                     '2026-08-08 11:08:03+00'),

    -- Ananya Iyer (high-mid) — abandoned checkout after a successful term
    ('6f4ec507-d9b6-4c90-b49b-35714c1ff3bb', '15366978-a54d-4079-b647-f33280d6d482', 19999.00, 'INR', 'succeeded', NULL,                    '2026-05-15 07:50:22+00', '2026-05-15 07:49:58+00'),
    ('6fb3d2ac-a3a2-42de-a5a6-5252ad6b5d6a', '15366978-a54d-4079-b647-f33280d6d482', 19999.00, 'INR', 'failed',    'checkout_abandoned',    NULL,                     '2026-08-15 07:52:41+00'),

    -- Rohan Desai (mid) — freelancer, payday NSF
    ('f6cd8afc-40b7-4b7e-b617-f907a987a8c9', 'b8f2be42-08c6-4252-8ca5-9498b35fe6cf',  4999.00, 'INR', 'succeeded', NULL,                    '2026-05-28 13:16:08+00', '2026-05-28 13:15:40+00'),
    ('75d2015a-1a89-4ec3-b45f-01a9a3dc9fae', 'b8f2be42-08c6-4252-8ca5-9498b35fe6cf',  4999.00, 'INR', 'succeeded', NULL,                    '2026-06-28 13:14:55+00', '2026-06-28 13:14:21+00'),
    ('61eb1b23-d0ff-4237-bd6a-d6313956006e', 'b8f2be42-08c6-4252-8ca5-9498b35fe6cf',  4999.00, 'INR', 'failed',    'insufficient_funds',    NULL,                     '2026-07-28 13:18:02+00'),

    -- Meera Joshi (mid) — healthy history, no failure
    ('f15b80e5-c964-49eb-b27b-9aa7b430d5eb', '327df75a-3b56-4a3d-bf83-44b91ee6683f',  2999.00, 'INR', 'succeeded', NULL,                    '2026-06-11 09:03:17+00', '2026-06-11 09:02:44+00'),
    ('364c8ed4-8b13-4108-a0bd-06713c3c1a32', '327df75a-3b56-4a3d-bf83-44b91ee6683f',  2999.00, 'INR', 'succeeded', NULL,                    '2026-08-11 09:04:09+00', '2026-08-11 09:03:38+00'),

    -- Kabir Khan (mid) — gym membership, card expired on renewal
    ('c9fbd89c-d097-43f3-9c8e-2fa81da74dee', 'a6312223-2465-4b1d-a6b1-c8598cfb86f8',  1999.00, 'INR', 'succeeded', NULL,                    '2026-05-20 16:32:11+00', '2026-05-20 16:31:40+00'),
    ('5005e88e-2fc9-485b-a0d8-fff45fc8b8ca', 'a6312223-2465-4b1d-a6b1-c8598cfb86f8',  1999.00, 'INR', 'succeeded', NULL,                    '2026-06-20 16:31:05+00', '2026-06-20 16:30:29+00'),
    ('80c57591-bdbb-48a9-8726-c5f4fb792e03', 'a6312223-2465-4b1d-a6b1-c8598cfb86f8',  1999.00, 'INR', 'failed',    'expired_card',          NULL,                     '2026-07-20 16:33:48+00'),

    -- Sneha Nair (mid) — bank error then paid the next cycle (recovered)
    ('b85742d0-526b-4253-b0b2-def4b8e7eeb4', '927ff09b-1e55-4f7b-8cc3-16a5e29d1960',  7999.00, 'INR', 'succeeded', NULL,                    '2026-03-07 05:56:22+00', '2026-03-07 05:55:51+00'),
    ('a5b99f04-6891-4ab2-8d19-e5d8b8df3e96', '927ff09b-1e55-4f7b-8cc3-16a5e29d1960',  7999.00, 'INR', 'failed',    'bank_error',            NULL,                     '2026-06-07 05:57:14+00'),
    ('349101cc-3d5f-4869-995e-28ee51b706bb', '927ff09b-1e55-4f7b-8cc3-16a5e29d1960',  7999.00, 'INR', 'succeeded', NULL,                    '2026-06-11 10:12:40+00', '2026-06-11 10:12:08+00'),

    -- Aditya Rao (mid) — 3DS failure on upgrade
    ('4f3755cf-e0a6-492e-bf84-0fde000e7df9', '6bfb8c60-fbb7-4a5c-a02d-f3986feb1fe3',  3999.00, 'INR', 'succeeded', NULL,                    '2026-04-03 12:11:33+00', '2026-04-03 12:10:59+00'),
    ('3d74a78c-bf84-426d-9a7b-92bc1056f666', '6bfb8c60-fbb7-4a5c-a02d-f3986feb1fe3',  3999.00, 'INR', 'failed',    'authentication_failed', NULL,                     '2026-08-03 12:13:07+00'),

    -- Fatima Ali (mid) — dropped off at checkout
    ('41f52942-014d-4156-b336-c98550304414', '137754ca-5217-47fc-a8c7-7a11bf678139',  5999.00, 'INR', 'succeeded', NULL,                    '2026-04-14 08:45:19+00', '2026-04-14 08:44:50+00'),
    ('e103af2d-0bf6-4c2a-858e-022887200be5', '137754ca-5217-47fc-a8c7-7a11bf678139',  5999.00, 'INR', 'succeeded', NULL,                    '2026-06-14 08:46:02+00', '2026-06-14 08:45:28+00'),
    ('1cd1ebe2-55b2-4a01-9ce8-7b085205d978', '137754ca-5217-47fc-a8c7-7a11bf678139',  5999.00, 'INR', 'failed',    'checkout_abandoned',    NULL,                     '2026-08-14 08:47:55+00'),

    -- Harsh Patel (mid) — all successful
    ('264c6801-e3f7-4bbd-b6d9-1156bcc668f5', 'e2adae4d-9298-48af-9e4e-c4cc184f046d',  2499.00, 'INR', 'succeeded', NULL,                    '2026-04-21 14:21:08+00', '2026-04-21 14:20:33+00'),
    ('a9b43138-764c-41ab-a879-d8347f309e1e', 'e2adae4d-9298-48af-9e4e-c4cc184f046d',  2499.00, 'INR', 'succeeded', NULL,                    '2026-07-21 14:22:41+00', '2026-07-21 14:22:10+00'),

    -- Diya Sharma (low) — NSF; weak recovery ROI
    ('ce41451b-c211-4adc-a417-77b3a25f5ceb', 'b04cd446-f930-4265-9060-2008ca9200b5',   499.00, 'INR', 'succeeded', NULL,                    '2026-06-16 18:03:22+00', '2026-06-16 18:02:51+00'),
    ('2af4c93b-8580-44a0-be57-896ecb9a1105', 'b04cd446-f930-4265-9060-2008ca9200b5',   499.00, 'INR', 'failed',    'insufficient_funds',    NULL,                     '2026-07-16 18:04:09+00'),

    -- Kunal Verma (low) — expired debit card
    ('65c5fccc-b2de-439f-9fd3-c81cd88fe938', '04643ea6-6f43-4f03-ab1e-7d731a6ef8c9',   299.00, 'INR', 'succeeded', NULL,                    '2026-06-02 11:12:18+00', '2026-06-02 11:11:44+00'),
    ('ab6dbb66-52ae-40db-ab49-8ee9e4be5828', '04643ea6-6f43-4f03-ab1e-7d731a6ef8c9',   299.00, 'INR', 'failed',    'expired_card',          NULL,                     '2026-08-02 11:13:01+00'),

    -- Aisha Khan (low) — all successful
    ('63090d00-d10b-4b5a-86c4-4f2f81c9c803', '545d0e1d-96ab-4c9b-a697-03b156c97bc5',   199.00, 'INR', 'succeeded', NULL,                    '2026-05-22 09:34:11+00', '2026-05-22 09:33:40+00'),
    ('808ecbf1-2e78-4b19-a377-af309194a05b', '545d0e1d-96ab-4c9b-a697-03b156c97bc5',   199.00, 'INR', 'succeeded', NULL,                    '2026-07-22 09:35:02+00', '2026-07-22 09:34:28+00'),

    -- Ravi Menon (low) — issuer bank error
    ('3dec6324-3317-4694-a4c0-f83479d1a5d4', '6577d41e-14f3-4324-961a-1ca3ac35cfff',   799.00, 'INR', 'succeeded', NULL,                    '2026-06-19 07:28:40+00', '2026-06-19 07:28:05+00'),
    ('c95d2b49-8d77-4426-98bd-36ec144e46bc', '6577d41e-14f3-4324-961a-1ca3ac35cfff',   799.00, 'INR', 'failed',    'bank_error',            NULL,                     '2026-08-19 07:29:17+00'),

    -- Pooja Gupta (low) — abandoned checkout
    ('bcd025c1-0bae-4423-ab9c-a4d8b927d97c', 'e82604f8-8248-49b8-898d-cd1c4b087048',   399.00, 'INR', 'succeeded', NULL,                    '2026-05-08 15:51:22+00', '2026-05-08 15:50:49+00'),
    ('f653e13a-754c-46ab-8b6f-04be9a3d4f8d', 'e82604f8-8248-49b8-898d-cd1c4b087048',   399.00, 'INR', 'failed',    'checkout_abandoned',    NULL,                     '2026-08-08 15:52:08+00'),

    -- Sameer Iqbal (low) — authentication failure
    ('14dccbd4-c5cf-480f-865b-ab032a830f1a', '63d10308-d781-4bd4-9940-28ed15b1ad7a',   599.00, 'INR', 'succeeded', NULL,                    '2026-05-27 10:06:33+00', '2026-05-27 10:05:58+00'),
    ('44e8b5bf-0166-40fd-9989-3f2b1d1d926b', '63d10308-d781-4bd4-9940-28ed15b1ad7a',   599.00, 'INR', 'failed',    'authentication_failed', NULL,                     '2026-08-16 10:08:14+00'),

    -- Tanya Bose (low) — checkout still pending (in-progress abandon)
    ('729742ab-e8bd-44f6-be33-0f30869f5a9b', '8b46c86f-5136-4cc4-b41a-26a0de54df7d',   249.00, 'INR', 'succeeded', NULL,                    '2026-07-01 19:19:08+00', '2026-07-01 19:18:31+00'),
    ('01375332-3022-434b-a995-200019ee2d83', '8b46c86f-5136-4cc4-b41a-26a0de54df7d',   249.00, 'INR', 'pending',   NULL,                    NULL,                     '2026-08-20 19:21:44+00'),

    -- Nikhil Jain (low) — NSF then paid a few days later (recovered)
    ('2492d54a-1970-4c4b-934b-30f6a1dc499f', '630cf89f-8620-4937-8aaa-e57b98ce3078',   999.00, 'INR', 'succeeded', NULL,                    '2026-04-30 06:41:22+00', '2026-04-30 06:40:50+00'),
    ('5e1f0b61-7403-4fe1-ae8b-c62c5b609253', '630cf89f-8620-4937-8aaa-e57b98ce3078',   999.00, 'INR', 'failed',    'insufficient_funds',    NULL,                     '2026-07-30 06:42:09+00'),
    ('a9b434df-c34c-46ad-9be0-7ca8bef4a509', '630cf89f-8620-4937-8aaa-e57b98ce3078',   999.00, 'INR', 'succeeded', NULL,                    '2026-08-04 09:15:40+00', '2026-08-04 09:15:11+00');

-- ---------------------------------------------------------------------------
-- recovery_cases — one per failed payment (not pending)
-- ---------------------------------------------------------------------------
INSERT INTO recovery_cases (
    id, customer_id, payment_id, status,
    amount_at_risk, amount_recovered, attempt_count,
    ai_decision, ai_decision_note, last_attempt_at, created_at, updated_at
) VALUES
    -- Priya: high-value expired card, retry in progress
    (
        '82b58739-8064-41cb-b9d4-5ad2ea228729',
        '5980614a-0ceb-4c26-84cf-08a688a135f2',
        '7f97ad41-1c29-4dfb-8cd6-d6712bb45143',
        'in_progress', 49999.00, 0, 1, 'retry',
        'Long success streak; card likely expired. Retry after asking for an updated card.',
        '2026-07-13 08:00:00+00', '2026-07-12 09:10:00+00', '2026-07-13 08:00:00+00'
    ),
    -- Arjun: NSF, waiting for payday
    (
        '3117b72c-cd6a-4684-95d7-65e10a06374f',
        'ad0e0a38-e907-46b3-b890-e0b5cbdff634',
        'f2dd2e82-724f-4cfa-b1bf-461aa6bd013c',
        'open', 24999.00, 0, 0, 'wait',
        'Prior payments succeeded. Wait until next salary cycle, then retry.',
        NULL, '2026-08-03 14:25:00+00', '2026-08-03 14:25:00+00'
    ),
    -- Nisha: large bank error, contact in progress
    (
        '11e8ca86-3cf8-48ce-8654-ad60ba0b66a9',
        'a8d8532d-9b88-467f-ba4b-a4f421888024',
        'cac0b71f-61ae-41c1-9d93-73444ee32a15',
        'in_progress', 89999.00, 0, 2, 'contact',
        'Issuer declined. High LTV — reach out and offer an alternate method.',
        '2026-08-20 05:30:00+00', '2026-08-18 06:50:00+00', '2026-08-20 05:30:00+00'
    ),
    -- Vikram: 3DS failure
    (
        '10ebc283-afe5-45c8-ad1f-0651aad246c7',
        '500053a7-2333-4ded-a65e-752bc23b2fc7',
        '0b2989a5-f12a-40c4-9ba3-9d83e3c413c9',
        'open', 34999.00, 0, 0, 'retry',
        'Authentication failed. Send a fresh payment link for 3DS.',
        NULL, '2026-08-08 11:15:00+00', '2026-08-08 11:15:00+00'
    ),
    -- Ananya: abandoned renewal
    (
        '561a04c9-16ac-4c15-b1f7-4ca7a1acbcab',
        '15366978-a54d-4079-b647-f33280d6d482',
        '6fb3d2ac-a3a2-42de-a5a6-5252ad6b5d6a',
        'open', 19999.00, 0, 0, 'contact',
        'Paid last term, dropped off at checkout. Reminder with one-click pay.',
        NULL, '2026-08-15 08:00:00+00', '2026-08-15 08:00:00+00'
    ),
    -- Rohan: NSF retry already attempted
    (
        '4d7f09ba-4a17-4b55-9798-4d8b7b51fed4',
        'b8f2be42-08c6-4252-8ca5-9498b35fe6cf',
        '61eb1b23-d0ff-4237-bd6a-d6313956006e',
        'in_progress', 4999.00, 0, 1, 'retry',
        'Two prior successes. Retry around month-end.',
        '2026-08-01 04:00:00+00', '2026-07-28 13:25:00+00', '2026-08-01 04:00:00+00'
    ),
    -- Kabir: expired card
    (
        '8f1c5e0f-8481-4763-988b-56287efa3622',
        'a6312223-2465-4b1d-a6b1-c8598cfb86f8',
        '80c57591-bdbb-48a9-8726-c5f4fb792e03',
        'open', 1999.00, 0, 0, 'contact',
        'Card expired. Ask customer to update payment method.',
        NULL, '2026-07-20 16:40:00+00', '2026-07-20 16:40:00+00'
    ),
    -- Sneha: bank error later collected
    (
        'ec56ebb2-eaeb-4f40-9562-473369112101',
        '927ff09b-1e55-4f7b-8cc3-16a5e29d1960',
        'a5b99f04-6891-4ab2-8d19-e5d8b8df3e96',
        'recovered', 7999.00, 7999.00, 1, 'close',
        'Retried after issuer outage; subsequent payment succeeded.',
        '2026-06-11 10:12:40+00', '2026-06-07 06:05:00+00', '2026-06-11 10:13:00+00'
    ),
    -- Aditya: auth failure
    (
        'b68db02c-9153-4491-ab9f-1b58e049b0bd',
        '6bfb8c60-fbb7-4a5c-a02d-f3986feb1fe3',
        '3d74a78c-bf84-426d-9a7b-92bc1056f666',
        'open', 3999.00, 0, 0, NULL,
        NULL,
        NULL, '2026-08-03 12:20:00+00', '2026-08-03 12:20:00+00'
    ),
    -- Fatima: abandoned checkout
    (
        'd71cd52a-bb2b-48fa-94d6-81c6f4c6fc85',
        '137754ca-5217-47fc-a8c7-7a11bf678139',
        '1cd1ebe2-55b2-4a01-9ce8-7b085205d978',
        'open', 5999.00, 0, 0, 'wait',
        'Two prior successes. Send a gentle reminder in 24h, not immediately.',
        NULL, '2026-08-14 09:00:00+00', '2026-08-14 09:00:00+00'
    ),
    -- Diya: low-value NSF — skip
    (
        '07e1a758-19e2-4bd9-a107-7d5ae6739c6a',
        'b04cd446-f930-4265-9060-2008ca9200b5',
        '2af4c93b-8580-44a0-be57-896ecb9a1105',
        'closed', 499.00, 0, 0, 'skip',
        'Amount too small relative to recovery cost.',
        NULL, '2026-07-16 18:10:00+00', '2026-07-16 18:10:00+00'
    ),
    -- Kunal: low-value expired card — skip
    (
        'b495d567-31da-4d09-88dc-00e9c096c224',
        '04643ea6-6f43-4f03-ab1e-7d731a6ef8c9',
        'ab6dbb66-52ae-40db-ab49-8ee9e4be5828',
        'closed', 299.00, 0, 0, 'skip',
        'Low ticket; do not spend recovery effort.',
        NULL, '2026-08-02 11:20:00+00', '2026-08-02 11:20:00+00'
    ),
    -- Ravi: bank error, retry
    (
        '7c3ed8e3-ddd7-4610-95bc-c3b247a9525b',
        '6577d41e-14f3-4324-961a-1ca3ac35cfff',
        'c95d2b49-8d77-4426-98bd-36ec144e46bc',
        'open', 799.00, 0, 0, 'retry',
        'Transient issuer error after a successful payment. Safe to retry.',
        NULL, '2026-08-19 07:35:00+00', '2026-08-19 07:35:00+00'
    ),
    -- Pooja: abandoned checkout
    (
        'b5588ae3-2950-4512-aae5-22a61f1e25ef',
        'e82604f8-8248-49b8-898d-cd1c4b087048',
        'f653e13a-754c-46ab-8b6f-04be9a3d4f8d',
        'open', 399.00, 0, 0, NULL,
        NULL,
        NULL, '2026-08-08 16:00:00+00', '2026-08-08 16:00:00+00'
    ),
    -- Sameer: auth failure
    (
        'cd65f112-2fd3-4529-beb1-e69c81656cd8',
        '63d10308-d781-4bd4-9940-28ed15b1ad7a',
        '44e8b5bf-0166-40fd-9989-3f2b1d1d926b',
        'open', 599.00, 0, 0, 'retry',
        'Customer completed 3DS before. Send a new auth link.',
        NULL, '2026-08-16 10:15:00+00', '2026-08-16 10:15:00+00'
    ),
    -- Nikhil: NSF later collected
    (
        'af7cb589-68b5-4dea-9ce3-b2f55006f684',
        '630cf89f-8620-4937-8aaa-e57b98ce3078',
        '5e1f0b61-7403-4fe1-ae8b-c62c5b609253',
        'recovered', 999.00, 999.00, 1, 'close',
        'Retried after a few days; customer paid.',
        '2026-08-04 09:15:40+00', '2026-07-30 06:50:00+00', '2026-08-04 09:16:00+00'
    );
