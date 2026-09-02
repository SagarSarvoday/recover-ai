-- Apply after 005_add_merchants_and_ownership.sql.
-- Map the existing single-merchant development tenant to its Razorpay Test Mode account.
-- Do not overwrite a non-null mapping that may have been configured deliberately.
UPDATE merchants
SET razorpay_account_id = 'acc_TTZWM0fniZWAbi'
WHERE id = '00000000-0000-0000-0000-000000000001'
  AND razorpay_account_id IS NULL;
