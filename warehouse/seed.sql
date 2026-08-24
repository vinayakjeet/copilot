-- Deterministic warehouse contents. Every "random" value comes from
-- warehouse.frac(seed), a hash of the row's own keys, so two provisions of the
-- same commit produce identical business aggregates regardless of plan shape.
-- Row numbering uses explicit ORDER BY for the same reason: a row_number over
-- an unordered scan would reassign identities whenever the planner changes its
-- mind, and gold eval answers depend on this data staying put.
--
-- Re-running replaces everything: truncate + reload inside one transaction.

BEGIN;

TRUNCATE warehouse.daily_city_metrics, warehouse.payouts, warehouse.partner_shifts,
         warehouse.ticket_messages, warehouse.support_tickets,
         warehouse.ratings, warehouse.coupon_redemptions, warehouse.refunds,
         warehouse.payments, warehouse.deliveries, warehouse.order_items,
         warehouse.orders, warehouse.coupons, warehouse.delivery_partners,
         warehouse.menu_items, warehouse.restaurants, warehouse.customers,
         warehouse.zones, warehouse.cities
RESTART IDENTITY CASCADE;

INSERT INTO warehouse.cities (city_id, city_name, state_code, state_name, region, is_tier1, population, launched_on)
VALUES
 (1,'Bengaluru','KA','Karnataka','south',true,13608000,DATE '2023-11-01'),
 (2,'Mumbai','MH','Maharashtra','west',true,21304000,DATE '2023-11-01'),
 (3,'Delhi','DL','Delhi','north',true,16753000,DATE '2023-11-15'),
 (4,'Hyderabad','TG','Telangana','south',true,10268000,DATE '2023-12-01'),
 (5,'Pune','MH','Maharashtra','west',false,6934000,DATE '2024-01-10'),
 (6,'Chennai','TN','Tamil Nadu','south',true,11569000,DATE '2024-02-01'),
 (7,'Kolkata','WB','West Bengal','east',true,14850000,DATE '2024-03-15'),
 (8,'Jaipur','RJ','Rajasthan','north',false,3952000,DATE '2024-06-01'),
 (9,'Ahmedabad','GJ','Gujarat','west',false,8267000,DATE '2024-08-20'),
 (10,'Lucknow','UP','Uttar Pradesh','north',false,3698000,DATE '2024-11-05'),
 (11,'Indore','MP','Madhya Pradesh','central',false,2153000,DATE '2025-02-14'),
 (12,'Kochi','KL','Kerala','south',false,2117000,DATE '2025-04-01');

INSERT INTO warehouse.zones (zone_id, city_id, zone_name, polygon_label, avg_delivery_minutes, is_high_demand)
SELECT c.city_id * 10 + z.n,
       c.city_id,
       c.city_name || ' Z' || z.n,
       'poly-' || lower(c.state_code) || '-' || z.n,
       round((18 + warehouse.frac(c.city_id * 97 + z.n) * 22)::numeric, 1),
       warehouse.frac(c.city_id * 31 + z.n) < 0.25
FROM warehouse.cities c
CROSS JOIN LATERAL generate_series(1, 3 + (hashtextextended(c.city_name, 0) & 1)) AS z(n);

INSERT INTO warehouse.customers (
    customer_id, full_name, email, phone_hash, city_id, signup_date, loyalty_tier,
    lifetime_value_inr, is_active, preferred_language, marketing_opt_in,
    last_order_date, acquisition_channel, device_type, birth_year, gender_reported)
SELECT i,
       (ARRAY['Aarav','Diya','Ishaan','Meera','Kabir','Anaya','Vivaan','Saanvi',
              'Aditya','Pooja','Arjun','Nisha','Rohan','Tara','Dev','Kavya'])[1 + (i * 29 % 16)]
       || ' '
       || (ARRAY['Sharma','Iyer','Reddy','Patel','Banerjee','Singh','Nair','Gupta',
                 'Rao','Joshi','Mehta','Das','Kulkarni','Chopra','Verma','Pillai'])
            [1 + (i * 37 % 16)],
       'customer' || i || '@example.in',
       md5('phone' || i::text),
       1 + (i * 13 % 12),
       DATE '2023-11-01' + (i * 7 % 900)::int,
       CASE WHEN warehouse.frac(i * 100 + 2) < 0.55 THEN 'none'
            WHEN warehouse.frac(i * 100 + 2) < 0.80 THEN 'silver'
            WHEN warehouse.frac(i * 100 + 2) < 0.95 THEN 'gold' ELSE 'platinum' END,
       CASE WHEN warehouse.frac(i * 100 + 3) < 0.12 THEN NULL
            ELSE round((warehouse.frac(i * 100 + 3) * 42000)::numeric, 2) END,
       warehouse.frac(i * 100 + 4) > 0.06,
       (ARRAY['hi','en','ta','kn','bn','mr'])[1 + (i * 101 % 6)],
       warehouse.frac(i * 100 + 4) < 0.45,
       CASE WHEN warehouse.frac(i * 100 + 3) < 0.85
            THEN DATE '2026-08-23' - (warehouse.frac(i * 100 + 5) * 90)::int END,
       (ARRAY['organic','referral','ads','influencer'])[1 + (i * 17 % 4)],
       (ARRAY['android','ios','web'])[1 + (i * 19 % 3)],
       1955 + (i * 23 % 45),
       CASE WHEN warehouse.frac(i * 100 + 2) < 0.52 THEN 'female'
            WHEN warehouse.frac(i * 100 + 2) < 0.96 THEN 'male' ELSE 'not_stated' END
FROM generate_series(1, 4000) AS i;

INSERT INTO warehouse.restaurants (
    restaurant_id, legal_name, display_name, cuisine_primary, cuisine_secondary, city_id,
    opened_on, is_veg_only, gstin, commission_pct, avg_prep_minutes, rating_avg,
    rating_count, min_order_value_inr, is_active, closure_date, parent_brand_id)
SELECT r.i,
       dn.display_name || ' Foods Pvt Ltd',
       dn.display_name || ' ' || c.city_name,
       cu.cuisine,
       CASE WHEN warehouse.frac(r.i * 200 + 2) < 0.4 THEN cu2.cuisine END,
       1 + (r.i * 13 % 12),
       DATE '2022-06-01' + (r.i * 7 % 800)::int,
       warehouse.frac(r.i * 200 + 2) < 0.28,
       '29' || chr(65 + (hashtextextended(r.i::text, 1) & 25)::int)
            || chr(65 + (hashtextextended(r.i::text, 2) & 25)::int)
            || chr(65 + (hashtextextended(r.i::text, 3) & 25)::int)
            || chr(65 + (hashtextextended(r.i::text, 4) & 25)::int)
            || chr(65 + (hashtextextended(r.i::text, 5) & 25)::int)
            || (1000 + hashtextextended(r.i::text, 6) & 8191)::text
            || chr(65 + (hashtextextended(r.i::text, 7) & 25)::int)
            || '1Z' || (hashtextextended(r.i::text, 8) % 10)::text,
       (ARRAY[15.00, 18.00, 22.00])[1 + (r.i * 23 % 3)],
       10 + (r.i * 29 % 21),
       round((3.1 + warehouse.frac(r.i * 200 + 4) * 1.85)::numeric, 2),
       hashtextextended(r.i::text, 9) & 4095,
       (ARRAY[49, 99, 149, 199])[1 + (r.i * 37 % 4)],
       warehouse.frac(r.i * 200 + 4) >= 0.07,
       CASE WHEN warehouse.frac(r.i * 200 + 4) < 0.07
            THEN DATE '2025-09-01' + (warehouse.frac(r.i * 200 + 5) * 300)::int END,
       NULL
FROM generate_series(1, 430) AS r(i)
JOIN LATERAL (SELECT c.city_name FROM warehouse.cities c
              WHERE c.city_id = 1 + (r.i * 13 % 12)) AS c(city_name) ON true,
LATERAL (SELECT (ARRAY['Biryani House','Dosa Corner','Tandoor Nights','Wok Street','Grill Junction',
                       'Thali Tales','Roll Republic','Curry Leaf Cafe','Momos Point','Pizza Darbar'])
               [1 + (r.i * 41 % 10)]) AS dn(display_name),
LATERAL (SELECT (ARRAY['North Indian','South Indian','Chinese','Mughlai','Continental'])
               [1 + (r.i * 43 % 5)]) AS cu(cuisine),
LATERAL (SELECT (ARRAY['North Indian','South Indian','Chinese','Mughlai','Continental'])
               [1 + (r.i * 47 % 5)]) AS cu2(cuisine);

UPDATE warehouse.restaurants
SET parent_brand_id = restaurant_id - 47
WHERE restaurant_id > 47 AND hashtextextended(restaurant_id::text, 53) % 8 = 0;

INSERT INTO warehouse.menu_items (
    item_id, restaurant_id, item_name, category, description, price_inr, is_available,
    is_jain_option, spice_level, calories_kcal, prep_minutes, image_url, created_at, updated_at)
WITH per_restaurant AS (
    SELECT r.restaurant_id, 5 + (hashtextextended(r.restaurant_id::text, 61) % 5) AS n
    FROM warehouse.restaurants r),
numbered AS (
    SELECT row_number() OVER (ORDER BY pr.restaurant_id, k.k) AS item_id,
           pr.restaurant_id
    FROM per_restaurant pr CROSS JOIN LATERAL generate_series(1, pr.n) AS k(k))
SELECT n.item_id,
       n.restaurant_id,
       nm.name || ' ' || sfx.suffix,
       cat.category,
       left(md5(n.item_id::text), 12),
       round((catb.base + (n.item_id * 71 % 240))::numeric, 2),
       warehouse.frac(n.item_id * 1000 + 2) > 0.12,
       warehouse.frac(n.item_id * 1000 + 2) < 0.18,
       (n.item_id * 73 % 4)::smallint,
       CASE WHEN warehouse.frac(n.item_id * 1000 + 3) < 0.2 THEN NULL
            ELSE 120 + (n.item_id * 79 % 680) END,
       6 + (n.item_id * 83 % 19),
       'img/' || left(md5(n.item_id::text), 10) || '.webp',
       TIMESTAMP '2024-01-05 09:00+00' + make_interval(days => (warehouse.frac(n.item_id * 1000 + 5) * 600)::int),
       TIMESTAMP '2026-06-01 09:00+00'
FROM numbered n,
LATERAL (SELECT (ARRAY['Starters','Main Course','Breads','Rice','Beverages','Desserts'])
               [1 + (n.item_id * 89 % 6)]) AS cat(category),
LATERAL (SELECT (ARRAY[60, 180, 45, 140, 90, 110])[1 + (n.item_id * 89 % 6)]) AS catb(base),
LATERAL (SELECT (ARRAY['Masala','Special','House','Royal','Street-style','Signature'])
               [1 + (n.item_id * 97 % 6)]) AS sfx(suffix),
LATERAL (SELECT (ARRAY['Paneer Tikka','Chicken 65','Butter Naan','Veg Biryani','Masala Chai','Gulab Jamun',
                       'Mutton Rogan Josh','Dal Tadka','Filter Coffee','Samosa Chaat','Fish Curry',
                       'Idli Sambar','Pav Bhaji','Tandoori Wings'])
               [1 + (n.item_id * 101 % 14)]) AS nm(name);

INSERT INTO warehouse.delivery_partners (
    partner_id, full_name, city_id, onboarded_on, vehicle_type, is_active, rating_avg,
    trips_lifetime, earnings_lifetime_inr, phone_hash, zone_preference, max_parallel_orders)
SELECT p.i,
       fn.name || ' ' || ln.name,
       pc.cid,
       DATE '2024-01-01' + (p.i * 7 % 700)::int,
       (ARRAY['bike','scooter','ev_bike','bicycle'])[1 + (p.i * 13 % 4)],
       warehouse.frac(p.i * 300 + 2) > 0.15,
       round((3.4 + warehouse.frac(p.i * 300 + 3) * 1.55)::numeric, 2),
       200 + (p.i * 17 % 3800),
       round((25000 + warehouse.frac(p.i * 300 + 3) * 260000)::numeric, 2),
       md5('partner' || p.i::text),
       CASE WHEN warehouse.frac(p.i * 300 + 4) < 0.6
            THEN pc.cid * 10 + 1 + (p.i * 19 % 3) END,
       (1 + (p.i * 23 % 3))::smallint
FROM generate_series(1, 600) AS p(i),
LATERAL (SELECT 1 + (p.i * 13 % 12)) AS pc(cid),
LATERAL (SELECT (ARRAY['Ramesh','Suresh','Lakshmi','Gopal','Manju','Farhan','Salma','Vikas',
                       'Umesh','Rekha'])[1 + (p.i * 29 % 10)]) AS fn(name),
LATERAL (SELECT (ARRAY['Kumar','Yadav','Sheikh','Pal','Mishra','Fernandes','Khan','Naik',
                       'Shetty','Dubey'])[1 + (p.i * 31 % 10)]) AS ln(name);

INSERT INTO warehouse.coupons (coupon_id, code, description, discount_type, discount_value,
                               min_order_inr, max_discount_inr, valid_from, valid_to, issued_by_campaign)
VALUES
 (1,'WELCOME50','Flat 50 off on your first order','flat',50,199,50,DATE '2025-03-01',DATE '2026-08-23','always-on'),
 (2,'SAVE20','20 percent off up to 120','percent',20,249,120,DATE '2025-03-01',DATE '2026-08-23','always-on'),
 (3,'MONSOON15','Monsoon cashback 15 percent','percent',15,199,80,DATE '2025-06-01',DATE '2025-09-15','monsoon-2025'),
 (4,'DIWALI25','Diwali festive 25 percent','percent',25,499,200,DATE '2025-10-10',DATE '2025-11-05','diwali-2025'),
 (5,'NEWYEAR40','New year flat 40 off','flat',40,299,40,DATE '2025-12-26',DATE '2026-01-10','ny-2026'),
 (6,'LUNCHXPRESS','Lunch hours 12 percent','percent',12,149,60,DATE '2025-04-01',DATE '2026-08-23','lunch-always'),
 (7,'RAINCHECK','Rain day free delivery credit','flat',30,179,30,DATE '2025-07-01',DATE '2025-08-30','monsoon-2025'),
 (8,'HOLI16','Holi special 16 percent','percent',16,249,90,DATE '2026-03-01',DATE '2026-03-10','holi-2026'),
 (9,'WEEKENDWOW','Weekend 18 percent','percent',18,299,110,DATE '2025-05-01',DATE '2026-08-23','weekends'),
 (10,'VIPFLAT75','Platinum members flat 75','flat',75,399,75,DATE '2025-09-01',DATE '2026-08-23','loyalty');

INSERT INTO warehouse.orders (
    order_id, customer_id, restaurant_id, placed_at, delivered_at, promised_at, status,
    item_total_inr, delivery_fee_inr, taxes_inr, discount_inr, grand_total_inr,
    payment_mode, coupon_id, platform_channel, is_first_order, delivery_instructions, promo_note)
WITH base AS (
    SELECT i AS order_id,
           1 + (i * 13 % 4000) AS customer_id,
           1 + (i * 7 % 430) AS restaurant_id,
           CASE WHEN warehouse.frac(i * 100 + 2) < 0.76 THEN 'delivered'
                WHEN warehouse.frac(i * 100 + 2) < 0.84 THEN 'cancelled'
                WHEN warehouse.frac(i * 100 + 2) < 0.885 THEN 'refunded'
                WHEN warehouse.frac(i * 100 + 2) < 0.93 THEN 'out_for_delivery'
                WHEN warehouse.frac(i * 100 + 2) < 0.975 THEN 'delivered'
                ELSE 'placed' END AS status,
           CASE WHEN warehouse.frac(i * 100 + 3) BETWEEN 0.40 AND 0.62
                THEN 1 + (i * 29 % 10) END AS coupon_id,
           (95 + power(hashtextextended(i::text, 31) & 1023, 1.18)) AS item_total
    FROM generate_series(1, 16000) AS g(i)),
timings AS (
    SELECT b.*,
           TIMESTAMP '2025-03-01 00:00+00'
             + (warehouse.frac(b.order_id * 100 + 2) * 540)::int * INTERVAL '1 day'
             + (CASE WHEN warehouse.frac(b.order_id * 100 + 3) < 0.18 THEN 8
                     WHEN warehouse.frac(b.order_id * 100 + 3) < 0.58 THEN 13
                     WHEN warehouse.frac(b.order_id * 100 + 3) < 0.88 THEN 20
                     ELSE 23 END) * INTERVAL '1 hour'
             + (warehouse.frac(b.order_id * 100 + 4) * 3600)::int * INTERVAL '1 second' AS placed_at
    FROM base b),
priced AS (
    SELECT t.*,
           round(t.item_total::numeric, 2) AS item_total_r,
           (ARRAY[0.00, 25.00, 35.00, 45.00])[1 + (t.order_id * 37 % 4)] AS fee,
           round(COALESCE(least(t.item_total * cp.pct_or_flat, cp.cap), 0), 2) AS discount
    FROM timings t
    LEFT JOIN LATERAL (
        SELECT CASE WHEN c.discount_type = 'percent' THEN c.discount_value / 100.0 ELSE 1.0 END AS pct_or_flat,
               COALESCE(c.max_discount_inr, 999999) AS cap
        FROM warehouse.coupons c WHERE c.coupon_id = t.coupon_id) cp ON true)
SELECT p.order_id,
       p.customer_id,
       p.restaurant_id,
       p.placed_at,
       CASE WHEN p.status IN ('delivered','refunded')
            THEN p.placed_at + make_interval(secs => (28 + warehouse.frac(p.order_id * 100 + 4) * 1920)::int) END,
       p.placed_at + INTERVAL '38 minutes',
       p.status,
       p.item_total_r,
       p.fee,
       round((p.item_total_r * 0.05)::numeric, 2),
       p.discount,
       round(p.item_total_r + p.fee + round((p.item_total_r * 0.05)::numeric, 2) - p.discount, 2),
       CASE WHEN warehouse.frac(p.order_id * 100 + 5) < 0.62 THEN 'upi'
            WHEN warehouse.frac(p.order_id * 100 + 5) < 0.80 THEN 'cod'
            WHEN warehouse.frac(p.order_id * 100 + 5) < 0.94 THEN 'card' ELSE 'netbanking' END,
       p.coupon_id,
       (ARRAY['android','ios','web'])[1 + (p.order_id * 23 % 3)],
       warehouse.frac(p.order_id * 100 + 6) < 0.17,
       CASE WHEN warehouse.frac(p.order_id * 100 + 6) < 0.32
            THEN (ARRAY['Ring the bell twice','Leave at door','Call on arrival',
                        'Do not add cutlery','Hand to guard'])[1 + (p.order_id % 5)] END,
       CASE WHEN warehouse.frac(p.order_id * 100 + 6) < 0.18
            THEN (ARRAY['birthday treat','office party','weekend craving',
                        'refer-and-earn','app-review promo'])[1 + (p.order_id * 3 % 5)] END
FROM priced p;

INSERT INTO warehouse.order_items (order_item_id, order_id, item_id, quantity, unit_price_inr, line_total_inr, notes)
WITH bounds AS (
    SELECT m.restaurant_id, min(m.item_id) AS lo, max(m.item_id) AS hi
    FROM warehouse.menu_items m GROUP BY m.restaurant_id),
expanded AS (
    SELECT oi.order_item_id,
           oi.order_id,
           b.lo + (warehouse.frac(oi.order_item_id * 500 + 13) * GREATEST(b.hi - b.lo, 0))::int AS item_id,
           1 + (oi.order_item_id * 7 % 3) AS quantity
    FROM (SELECT row_number() OVER (ORDER BY o.order_id, k.k) AS order_item_id,
                 o.order_id, o.restaurant_id
          FROM warehouse.orders o
          CROSS JOIN LATERAL generate_series(
              1, 1 + (warehouse.frac(o.order_id * 500 + 3) * 3)::int) AS k(k)
          WHERE o.status <> 'placed') oi
    JOIN bounds b USING (restaurant_id))
SELECT e.order_item_id,
       e.order_id,
       e.item_id,
       e.quantity,
       m.price_inr,
       round((m.price_inr * e.quantity)::numeric, 2),
       CASE WHEN warehouse.frac(e.order_item_id * 500 + 5) < 0.08 THEN 'no onion no garlic'
            WHEN warehouse.frac(e.order_item_id * 500 + 5) < 0.12 THEN 'extra spicy' END
FROM expanded e
JOIN warehouse.menu_items m USING (item_id);

INSERT INTO warehouse.deliveries (
    delivery_id, order_id, partner_id, assigned_at, picked_up_at, delivered_at, distance_km,
    partner_tip_inr, wait_minutes_at_restaurant, attempts, failure_reason, is_late)
WITH eligible AS (
    SELECT row_number() OVER (ORDER BY o.order_id) AS delivery_id,
           o.order_id, o.placed_at, o.delivered_at, o.promised_at, r.city_id
    FROM warehouse.orders o
    JOIN warehouse.restaurants r USING (restaurant_id)
    WHERE o.status NOT IN ('placed')),
pbounds AS (
    SELECT p.city_id, min(p.partner_id) AS lo, max(p.partner_id) AS hi
    FROM warehouse.delivery_partners p GROUP BY p.city_id)
SELECT e.delivery_id,
       e.order_id,
       pb.lo + (warehouse.frac(e.delivery_id * 600 + 13) * GREATEST(pb.hi - pb.lo, 0))::int,
       e.placed_at + make_interval(secs => (70 + warehouse.frac(e.delivery_id * 600 + 1) * 240)::int),
       e.placed_at + make_interval(secs => (70 + warehouse.frac(e.delivery_id * 600 + 1) * 240)::int)
           + make_interval(mins => round(warehouse.frac(e.delivery_id * 600 + 3) * 18)::int),
       e.delivered_at,
       round((0.6 + warehouse.frac(e.delivery_id * 600 + 1) * 7.4)::numeric, 2),
       CASE WHEN warehouse.frac(e.delivery_id * 600 + 2) < 0.15
            THEN round((5 + warehouse.frac(e.delivery_id * 600 + 2) * 220)::numeric / 6, 0) ELSE 0 END,
       round(warehouse.frac(e.delivery_id * 600 + 3) * 18)::int,
       CASE WHEN warehouse.frac(e.delivery_id * 600 + 4) < 0.055 THEN 2 ELSE 1 END,
       CASE WHEN warehouse.frac(e.delivery_id * 600 + 4) < 0.055
            THEN (ARRAY['partner_unavailable','address_not_found','customer_unreachable'])
                 [1 + (e.delivery_id * 17 % 3)] END,
       CASE WHEN e.delivered_at IS NOT NULL THEN e.delivered_at > e.promised_at END
FROM eligible e
JOIN pbounds pb USING (city_id);

INSERT INTO warehouse.payments (
    payment_id, order_id, gateway, method, amount_inr, status, initiated_at, settled_at,
    settlement_batch_id, failure_code, is_split, gateway_ref)
WITH pay AS (
    SELECT row_number() OVER (ORDER BY o.order_id) AS payment_id,
           o.order_id, o.grand_total_inr, o.placed_at, o.payment_mode, o.status
    FROM warehouse.orders o)
SELECT p.payment_id,
       p.order_id,
       CASE WHEN p.payment_mode = 'cod' THEN 'internal_cod'
            WHEN warehouse.frac(p.payment_id * 700 + 1) < 0.5 THEN 'razorpay'
            WHEN warehouse.frac(p.payment_id * 700 + 1) < 0.8 THEN 'cashfree' ELSE 'payu' END,
       p.payment_mode,
       p.grand_total_inr,
       CASE WHEN p.payment_mode = 'cod' THEN CASE WHEN p.status = 'cancelled' THEN 'failed' ELSE 'captured' END
            WHEN p.status = 'cancelled' AND warehouse.frac(p.payment_id * 700 + 1) < 0.5 THEN 'failed'
            WHEN p.status IN ('placed','confirmed','preparing') THEN 'authorized'
            ELSE 'captured' END,
       p.placed_at + INTERVAL '4 seconds',
       CASE WHEN p.payment_mode <> 'cod' AND p.status NOT IN ('placed','confirmed','preparing','cancelled')
            THEN p.placed_at + make_interval(hours => (warehouse.frac(p.payment_id * 700 + 2) * 40 + 6)::int) END,
       CASE WHEN p.payment_mode <> 'cod' AND p.status NOT IN ('placed','confirmed','preparing','cancelled')
            THEN 'batch-' || to_char(date_trunc('day', p.placed_at), 'YYYYMMDD') END,
       CASE WHEN p.payment_mode <> 'cod' AND p.status = 'cancelled'
                 AND warehouse.frac(p.payment_id * 700 + 1) < 0.5
            THEN (ARRAY['insufficient_funds','auth_declined','network_timeout','upi_collect_expired'])
                 [1 + (p.order_id * 13 % 4)] END,
       warehouse.frac(p.payment_id * 700 + 3) > 0.93,
       'pay_' || substr(md5(p.order_id::text), 1, 14)
FROM pay p;

INSERT INTO warehouse.refunds (refund_id, payment_id, order_id, reason, amount_inr, requested_at, processed_at, status, resolved_by_role)
WITH rf AS (
    SELECT row_number() OVER (ORDER BY o.order_id) AS refund_id,
           pm.payment_id, o.order_id, o.delivered_at, o.grand_total_inr
    FROM warehouse.orders o
    JOIN warehouse.payments pm USING (order_id)
    WHERE o.status = 'refunded')
SELECT r.refund_id,
       r.payment_id,
       r.order_id,
       (ARRAY['wrong_item','missing_item','late_delivery','quality_issue','payment_debited_no_order'])
       [1 + (r.order_id * 7 % 5)],
       round(r.grand_total_inr * CASE
             WHEN warehouse.frac(r.refund_id * 800 + 1) < 0.6 THEN 1.0
             WHEN warehouse.frac(r.refund_id * 800 + 1) < 0.85 THEN 0.5 ELSE 0.3 END, 2),
       r.delivered_at + make_interval(mins => (120 + warehouse.frac(r.refund_id * 800 + 1) * 3600)::int),
       r.delivered_at + make_interval(mins => (480 + warehouse.frac(r.refund_id * 800 + 2) * 2400)::int),
       CASE WHEN warehouse.frac(r.refund_id * 800 + 1) < 0.86 THEN 'processed'
            WHEN warehouse.frac(r.refund_id * 800 + 1) < 0.95 THEN 'approved' ELSE 'rejected' END,
       'ops'
FROM rf r;

INSERT INTO warehouse.coupon_redemptions (redemption_id, coupon_id, order_id, customer_id, discount_applied_inr, redeemed_at)
SELECT row_number() OVER (ORDER BY o.order_id),
       o.coupon_id, o.order_id, o.customer_id, o.discount_inr, o.placed_at
FROM warehouse.orders o
WHERE o.coupon_id IS NOT NULL;

INSERT INTO warehouse.ratings (rating_id, order_id, customer_id, restaurant_rating, partner_rating, comment, created_at, is_anonymous, response_from_restaurant)
WITH rt AS (
    SELECT row_number() OVER (ORDER BY o.order_id) AS rating_id,
           o.order_id, o.customer_id, o.delivered_at
    FROM warehouse.orders o
    WHERE o.status = 'delivered' AND hashtextextended(o.order_id::text, 13) % 100 < 34)
SELECT r.rating_id,
       r.order_id,
       r.customer_id,
       LEAST(5, GREATEST(1, ROUND(1 + power(warehouse.frac(r.rating_id * 900 + 1), 0.6) * 4.4)))::smallint,
       CASE WHEN warehouse.frac(r.rating_id * 900 + 2) > 0.28
            THEN LEAST(5, GREATEST(1, ROUND(1 + power(warehouse.frac(r.rating_id * 900 + 3), 0.5) * 4.6))::smallint) END,
       CASE WHEN warehouse.frac(r.rating_id * 900 + 2) < 0.22
            THEN (ARRAY['Great food, hot on arrival.','Late but the partner apologised nicely.',
                        'Portion felt smaller than usual.','Packaging leaked a little.',
                        'Absolutely perfect biryani.','Average, expected more.'])
                 [1 + (r.order_id * 17 % 6)] END,
       r.delivered_at + make_interval(mins => (10 + warehouse.frac(r.rating_id * 900 + 4) * 600)::int),
       warehouse.frac(r.rating_id * 900 + 3) < 0.12,
       CASE WHEN warehouse.frac(r.rating_id * 900 + 3) < 0.18 THEN 'Thank you for ordering with us.' END
FROM rt r;

INSERT INTO warehouse.support_tickets (ticket_id, order_id, customer_id, category, priority, status, opened_at, first_response_at, resolved_at, csat_score, channel, agent_id)
WITH tk AS (
    SELECT row_number() OVER (ORDER BY o.order_id) AS ticket_id,
           o.order_id, o.customer_id, o.placed_at
    FROM warehouse.orders o
    WHERE hashtextextended(o.order_id::text, 7) % 100 < 7)
SELECT t.ticket_id,
       t.order_id,
       t.customer_id,
       (ARRAY['order_late','missing_item','refund_followup','app_issue','food_quality','other'])
       [1 + (t.order_id * 13 % 6)],
       CASE WHEN warehouse.frac(t.ticket_id * 400 + 1) < 0.15 THEN 'urgent'
            WHEN warehouse.frac(t.ticket_id * 400 + 1) < 0.45 THEN 'high'
            WHEN warehouse.frac(t.ticket_id * 400 + 1) < 0.8 THEN 'normal' ELSE 'low' END,
       CASE WHEN warehouse.frac(t.ticket_id * 400 + 2) < 0.82 THEN 'closed'
            WHEN warehouse.frac(t.ticket_id * 400 + 2) < 0.92 THEN 'resolved'
            WHEN warehouse.frac(t.ticket_id * 400 + 2) < 0.97 THEN 'pending' ELSE 'open' END,
       t.placed_at + make_interval(hours => (72 + warehouse.frac(t.ticket_id * 400 + 3) * 72)::int),
       t.placed_at + make_interval(mins => (4320 + 5 + warehouse.frac(t.ticket_id * 400 + 1) * 130)::int),
       t.placed_at + make_interval(mins => (4320 + 120 + warehouse.frac(t.ticket_id * 400 + 3) * 2760)::int),
       CASE WHEN warehouse.frac(t.ticket_id * 400 + 2) < 0.82
            THEN LEAST(5, GREATEST(1, ROUND(2 + warehouse.frac(t.ticket_id * 400 + 3) * 3.4)))::smallint END,
       (ARRAY['chat','call','email'])[1 + (t.order_id * 17 % 3)],
       'AGT-' || lpad((1 + t.order_id * 19 % 38)::text, 2, '0')
FROM tk t;

INSERT INTO warehouse.ticket_messages (message_id, ticket_id, sender_role, body, sent_at, is_internal)
WITH lines AS (
    SELECT row_number() OVER (ORDER BY t.ticket_id, k.k) AS message_id,
           t.ticket_id, t.opened_at, t.resolved_at,
           k.k,
           warehouse.frac(t.ticket_id * 400 + k.k) AS fr
    FROM warehouse.support_tickets t
    CROSS JOIN LATERAL generate_series(1, 2 + (hashtextextended(t.ticket_id::text, 5) % 3)) AS k(k))
SELECT l.message_id,
       l.ticket_id,
       CASE WHEN l.k = 1 THEN 'customer'
            WHEN l.k = 2 THEN 'agent'
            ELSE 'system' END,
       CASE WHEN l.k = 1
            THEN (ARRAY['Where is my order?','This is not what I ordered.','Please escalate.',
                        'The app shows delivered but nothing arrived.','Refund please.'])
                 [1 + (l.message_id * 3 % 5)]
            WHEN l.k = 2
            THEN (ARRAY['Checking with the restaurant now.','Sorry about that, raising a refund.',
                        'Partner is 5 minutes away.','Escalated to ops, ticket updated.'])
                 [1 + (l.message_id * 7 % 4)]
            ELSE 'status auto-updated by workflow' END,
       CASE WHEN l.k = 1 THEN l.opened_at + make_interval(mins => (l.k * 3 + l.fr * 20)::int)
            WHEN l.k = 2 THEN l.opened_at + make_interval(mins => (l.k * 7 + l.fr * 40)::int)
            ELSE COALESCE(l.resolved_at, l.opened_at + INTERVAL '2 hours') END,
       l.k = 2 AND warehouse.frac(l.message_id * 400 + 9) < 0.2
FROM lines l;

INSERT INTO warehouse.partner_shifts (shift_id, partner_id, started_at, ended_at, orders_completed, incentives_inr, city_id)
WITH pairs AS (
    SELECT p.partner_id, p.city_id, d AS day_no
    FROM warehouse.delivery_partners p
    CROSS JOIN generate_series(0, 76) AS d
    WHERE hashtextextended(p.partner_id::text, 1000 + d) % 3 = 0),
shifted AS (
    SELECT row_number() OVER (ORDER BY pr.partner_id, pr.day_no) AS shift_id, pr.*
    FROM pairs pr)
SELECT s.shift_id,
       s.partner_id,
       TIMESTAMP '2025-03-01 00:00+00' + s.day_no * INTERVAL '3 days'
           + make_interval(hours => (8 + warehouse.frac(s.shift_id * 300 + 1) * 4)::int),
       TIMESTAMP '2025-03-01 00:00+00' + s.day_no * INTERVAL '3 days'
           + make_interval(hours => (8 + warehouse.frac(s.shift_id * 300 + 1) * 4)::int)
           + make_interval(hours => (6 + warehouse.frac(s.shift_id * 300 + 2) * 5)::int),
       4 + (s.shift_id * 7 % 15),
       round(((hashtextextended(s.shift_id::text, 11) & 255))::numeric, 2),
       s.city_id
FROM shifted s;

INSERT INTO warehouse.payouts (payout_id, restaurant_id, period_start, period_end, gross_inr, commission_inr, tcs_inr, net_inr, paid_on, status)
WITH months AS (
    SELECT gs::date AS period_start,
           (gs + INTERVAL '1 month - 1 day')::date AS period_end
    FROM generate_series(TIMESTAMP '2025-03-01', TIMESTAMP '2026-07-01', INTERVAL '1 month') gs),
agg AS (
    SELECT r.restaurant_id, r.commission_pct, m.period_start, m.period_end,
           sum(o.item_total_inr) FILTER (
               WHERE o.placed_at >= m.period_start::timestamptz
                 AND o.placed_at < (m.period_end + 1)::timestamptz) AS gross
    FROM warehouse.restaurants r
    CROSS JOIN months m
    LEFT JOIN warehouse.orders o ON o.restaurant_id = r.restaurant_id
    GROUP BY r.restaurant_id, r.commission_pct, m.period_start, m.period_end)
SELECT row_number() OVER (ORDER BY a.restaurant_id, a.period_start),
       a.restaurant_id, a.period_start, a.period_end,
       a.gross,
       round(a.gross * a.commission_pct / 100, 2),
       round(a.gross * 0.005, 2),
       round(a.gross * (1 - a.commission_pct / 100 - 0.005), 2),
       a.period_end + 5,
       'paid'
FROM agg a
WHERE a.gross IS NOT NULL;

INSERT INTO warehouse.daily_city_metrics (
    stat_date, city_id, orders_count, gmv_inr, avg_delivery_minutes, cancel_rate,
    new_customers, active_partners, rain_flag, festival_flag, on_time_pct, refund_rate)
WITH days AS (
    SELECT generate_series(DATE '2025-03-01', DATE '2026-08-23', INTERVAL '1 day')::date AS d),
festivals(d) AS (
    VALUES (DATE '2025-03-14'), (DATE '2025-08-15'), (DATE '2025-10-21'), (DATE '2025-11-01'),
           (DATE '2026-01-26'), (DATE '2026-03-03'), (DATE '2026-08-15')),
oagg AS (
    SELECT o.placed_at::date AS d, r.city_id,
           count(*) AS orders_count,
           sum(o.grand_total_inr) AS gmv,
           avg(EXTRACT(epoch FROM (dd.delivered_at - dd.assigned_at)) / 60)
               FILTER (WHERE dd.is_late IS NOT NULL) AS avg_min,
           count(*) FILTER (WHERE o.status = 'cancelled')::numeric / count(*) AS cancel_rate,
           count(*) FILTER (WHERE o.status = 'refunded')::numeric / count(*) AS refund_rate,
           count(*) FILTER (WHERE dd.is_late = false)::numeric
             / GREATEST(count(*) FILTER (WHERE dd.is_late IS NOT NULL), 1) AS on_time_frac
    FROM warehouse.orders o
    JOIN warehouse.restaurants r USING (restaurant_id)
    LEFT JOIN warehouse.deliveries dd USING (order_id)
    GROUP BY 1, 2),
nagg AS (
    SELECT signup_date AS d, city_id, count(*) AS new_customers
    FROM warehouse.customers GROUP BY 1, 2),
pagg AS (
    SELECT o.placed_at::date AS d, r.city_id, count(DISTINCT dd.partner_id) AS active_partners
    FROM warehouse.orders o
    JOIN warehouse.restaurants r USING (restaurant_id)
    JOIN warehouse.deliveries dd USING (order_id)
    WHERE dd.partner_id IS NOT NULL
    GROUP BY 1, 2)
SELECT d.d,
       c.city_id,
       COALESCE(o.orders_count, 0),
       COALESCE(round(o.gmv, 2), 0),
       round((COALESCE(o.avg_min, 0) + CASE WHEN COALESCE(rr.rain, false) THEN 4 + warehouse.frac((d.d - DATE '2025-03-01')::int * 100 + c.city_id) * 1.5 ELSE 0 END)::numeric, 1),
       round(COALESCE(o.cancel_rate, 0), 4),
       COALESCE(n.new_customers, 0),
       COALESCE(p.active_partners, 0),
       COALESCE(rr.rain, false),
       EXISTS (SELECT 1 FROM festivals f WHERE f.d = d.d),
       round((COALESCE(o.on_time_frac, 0) * 100
             * (0.94 + (warehouse.frac((d.d - DATE '2025-03-01')::int * 100 + c.city_id * 3))))::numeric, 2),
       round(COALESCE(o.refund_rate, 0), 4)
FROM days d
CROSS JOIN warehouse.cities c
LEFT JOIN oagg o ON o.d = d.d AND o.city_id = c.city_id
LEFT JOIN nagg n ON n.d = d.d AND n.city_id = c.city_id
LEFT JOIN pagg p ON p.d = d.d AND p.city_id = c.city_id
LEFT JOIN LATERAL (
    SELECT warehouse.frac((d.d - DATE '2025-03-01')::int * 100 + c.city_id * 7) < 0.14 AS rain
    WHERE EXTRACT(month FROM d.d) IN (6, 7, 8, 9)) rr(rain) ON true
WHERE o.orders_count IS NOT NULL OR n.new_customers IS NOT NULL;

COMMIT;
