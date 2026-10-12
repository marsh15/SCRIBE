"""Global word banks and per-topic lexicons for the Scribe 2.0 distractor corpus.

Everything here is plain data. Vocabulary is screened at import time by build_distractors.py
(absent-topic keywords, gold identifiers, soft-avoid phrases) so a bad word fails fast.
"""
from __future__ import annotations

FIRST = ("Aileen Bram Cedric Dagmar Evert Fenna Gideon Hanne Ilse Joost Karolina Lennart Mirela Noor Oskar "
         "Pieter Quinn Rosalie Stefan Tessa Ulrik Vera Willem Xaver Yara Zoran Annelies Bastiaan Corinne Dorian "
         "Esmee Florian Greta Hugo Isolde Jasper Kristin Leon Marit Niels Odile Pavel Renske Sander Tamsin "
         "Ursula Viktor Wilma Anouk Boris Carmen Daan Elif Femke Gregor Henrik Ingrid Jelle Katja Luuk").split()
LAST = ("Aalders Bakker Coenen Dekker Eriksen Fontaine Grobler Haverkamp Imhof Janssens Kuiper Lindqvist "
        "Moreau Nieuwenhuis Olsen Pietersen Quaedvlieg Rademaker Schouten Terpstra Uytterhoeven Vermeulen "
        "Wouters Yilmaz Zielinski Albers Berger Castellan Dumont Egberts Falkenberg Gerritsen Hoekstra "
        "Ivanova Jansen Koster Lambrecht Mulder Nowicki Oosterhuis Prinsen Reinders Smit Timmermans "
        "Vandenberg Wiersma Xenakis Zeelenberg Bosch Claes Driessen Evers Fischer Gomes Hartog").split()

CORE_SITES = ["Rotterdam", "Hamburg", "Antwerp", "Lyon", "Milan", "Warsaw", "Austin"]
EXTRA_SITES = ["the Utrecht transfer yard", "the Ghent satellite yard", "the Dortmund overflow yard",
               "the Turin annex", "the Poznan returns centre", "the Dallas overflow lot"]
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]
QUARTERS = ["Q1 2025", "Q2 2025", "Q3 2025", "Q4 2025", "Q1 2026", "Q2 2026", "Q3 2026"]

SUPPLIERS = [
    ("Brennholt Fleet Services GmbH", "Dortmund"), ("Quayside Packaging N.V.", "Ghent"),
    ("Lindqvist Kylteknik AB", "Gothenburg"), ("Marais Equipements SAS", "Lille"),
    ("Vistula Pallet Systems sp. z o.o.", "Poznan"), ("Ostrander Facility Care B.V.", "Utrecht"),
    ("Kestrel Fuel Cards Ltd", "Leeds"), ("Alpenrose Catering AG", "Zurich"),
    ("Portico Label Works S.r.l.", "Turin"), ("Haldane Calibration Ltd", "Glasgow"),
    ("Bluewater Shuttle Services B.V.", "Breda"), ("Nordhafen IT Leasing GmbH", "Bremen"),
    ("Tessera Training Partners S.L.", "Valencia"), ("Corvid Signage & Print Ltd", "Leicester"),
    ("Mistral Charging Solutions SAS", "Grenoble"), ("Greywether Document Storage Ltd", "Bristol"),
    ("Aurelia Workwear Supplies S.A.", "Antwerp"), ("Fennimore Handling Equipment Inc.", "Dallas"),
]
LAWS = ["German law", "Belgian law", "Swedish law", "French law", "Polish law", "the law of England and Wales"]
CUSTOMERS = ["Skarsgaard Foods AS", "Delmare Home & Garden BV", "Ruthven Pharma Ltd", "Pinewood Outdoor GmbH",
             "Calder & Voss SA", "Harrowgate Books Ltd", "Eastlake Electronics Inc."]

TOOLWORDS = ["queue", "dashboard", "register", "tracker", "console", "desk", "board", "ledger", "planner", "gateway"]

# ---------------------------------------------------------------------------------------------
# Topics. things / roles / systems / subs are '|' separated. csv = [(slug, [(column, spec), ...])]
# spec: id:PFX  site  date  person  thing  int:lo:hi  eur:lo:hi  dec:lo:hi:nd  choice:a/b/c  clock  pct  note
# ---------------------------------------------------------------------------------------------
def T(slug, name, prefix, things, roles, systems, subs, csv):
    return dict(slug=slug, name=name, prefix=prefix, things=things.split("|"), roles=roles.split("|"),
                systems=systems.split("|"), subs=subs.split("|"), csv=csv)


TOPICS = [
    T("cafeteria-catering", "Cafeteria and Catering", "CAT",
      "lunch menu|allergen label|meal token|coffee machine|vending contract|catering order|buffet setup|dietary request|"
      "kitchen inspection|supplier delivery|tray return|breakfast cart|event catering quote|water dispenser|compost bin|"
      "canteen price list|serving counter|cutlery pack",
      "catering coordinator|canteen supervisor|kitchen lead|facilities planner|site services manager|food safety officer",
      "MealPass|CaterDesk|KitchenLog",
      "Menu planning|Allergen management|Catering for meetings and events|Vending and coffee points|Kitchen hygiene checks|"
      "Supplier deliveries|Waste sorting at the canteen|Opening hours and queue management",
      [("catering-orders", [("order_id", "id:CAT"), ("site", "site"), ("order_date", "date"), ("item", "thing"),
                           ("guests", "int:4:180"), ("price_eur", "eur:35:1800"), ("status", "choice:confirmed/delivered/cancelled/queried")]),
       ("canteen-sales-weekly", [("week", "int:1:52"), ("site", "site"), ("meals_served", "int:120:2400"), ("coffee_cups", "int:80:1900"),
                                 ("avg_ticket_eur", "dec:3:9:2"), ("complaints", "int:0:9")])]),
    T("parking-shuttle", "Parking and Shuttle", "PRK",
      "parking permit|visitor bay|shuttle timetable|charging bay reservation|permit renewal|barrier fault|bicycle shed key|"
      "overflow lot|disabled bay|shuttle stop|boom gate card|lost ticket|permit transfer|car-share slot|loading bay marking",
      "parking administrator|shuttle coordinator|gate supervisor|site services manager|facilities planner|security desk officer",
      "ParkWise|ShuttleBoard|GateLog",
      "Permit allocation|Visitor parking|Shuttle routes and timetables|Barrier and gate faults|Bicycle storage|Overflow arrangements|Fines and warnings",
      [("parking-permits", [("permit_no", "id:PRK"), ("holder", "person"), ("site", "site"), ("valid_from", "date"),
                            ("bay_zone", "choice:A/B/C/D/Visitor"), ("plate_region", "choice:NL/DE/BE/FR/IT/PL/US"), ("status", "choice:active/expired/suspended")]),
       ("shuttle-runs", [("run_id", "id:SHT"), ("route", "choice:Yard-Station/Station-Yard/Annex-Main/Main-Annex"), ("departure", "clock"),
                         ("passengers", "int:0:19"), ("delay_min", "int:0:22"), ("driver", "person")])]),
    T("it-asset-lifecycle", "IT Asset Lifecycle", "ITA",
      "laptop image|asset tag|warranty extension|docking station|monitor order|mobile handset|loan device|repair ticket|"
      "disposal certificate|software licence|printer cartridge|network switch|spare charger|scanner firmware|asset audit",
      "asset manager|service desk lead|procurement analyst|IT operations manager|device engineer|licence administrator",
      "AssetHub|FixQueue|LicenceLedger",
      "Procurement and imaging|Tagging and registration|Warranty and repair|Loan pool devices|Software licences|Audit and reconciliation|Secure disposal",
      [("asset-register", [("asset_tag", "id:ITA"), ("category", "choice:laptop/monitor/handset/scanner/printer/switch/dock"), ("site", "site"),
                           ("purchased", "date"), ("cost_eur", "eur:90:2400"), ("warranty_months", "int:12:60"), ("state", "choice:in use/loan pool/in repair/awaiting disposal")]),
       ("repair-tickets", [("ticket", "id:FIX"), ("asset_tag", "id:ITA"), ("opened", "date"), ("fault", "note"), ("days_open", "int:0:41"), ("outcome", "choice:repaired/replaced/written off/pending")])]),
    T("marketing-campaigns", "Marketing Campaigns", "MKT",
      "campaign brief|landing page|trade show booth|customer newsletter|case study|press release|social post schedule|"
      "brand template|webinar|lead list|sponsorship|banner set|video script|survey invitation|partner leaflet",
      "marketing manager|campaign lead|content editor|events coordinator|brand officer|market analyst",
      "CampaignBoard|AssetVault|LeadTrack",
      "Briefing and approvals|Channels and calendar|Events and trade shows|Content production|Lead handling|Budget tracking|Measurement and reporting",
      [("campaign-spend", [("campaign", "id:MKT"), ("channel", "choice:email/web/event/print/social/partner"), ("start", "date"), ("budget_eur", "eur:800:42000"),
                           ("spent_eur", "eur:300:42000"), ("leads", "int:0:640"), ("owner", "person")]),
       ("newsletter-metrics", [("issue", "id:NWS"), ("sent", "date"), ("recipients", "int:900:6400"), ("opens_pct", "dec:18:61:1"), ("clicks_pct", "dec:1:14:1"), ("unsubscribes", "int:0:31")])]),
    T("training-catalog", "Training Course Catalogue", "TRN",
      "induction module|forklift refresher|spreadsheet workshop|first-line leadership course|language class|warehouse safety quiz|"
      "route planning seminar|customer care workshop|cold-room handling course|e-learning bundle|mentoring circle|compliance refresher|trainer-led session|certificate renewal|mock audit exercise",
      "learning coordinator|training manager|depot trainer|team leader|course author|workforce planner",
      "LearnPath|CourseDesk|SkillMatrix",
      "Course structure|Booking and cancellation|Trainer qualifications|Assessment and certificates|Language courses|Leadership programme|Evaluation of courses",
      [("course-sessions", [("session", "id:TRN"), ("course", "thing"), ("site", "site"), ("date", "date"), ("seats", "int:6:24"), ("booked", "int:0:24"), ("trainer", "person")]),
       ("training-completions", [("learner", "person"), ("course", "thing"), ("completed", "date"), ("score_pct", "int:55:100"), ("attempts", "int:1:3"), ("site", "site")])]),
    T("shift-rosters", "Depot Shift Rosters", "ROS",
      "early shift|late shift|night shift|weekend cover|swap request|overtime slot|break schedule|team rota|standby list|"
      "handover note|shift lead|dock crew|picking crew|loader team|absence notice",
      "shift planner|depot supervisor|team leader|workforce planner|dock lead|HR coordinator",
      "RosterPro|ShiftSwap|TimeBoard",
      "Roster publication|Shift swaps|Overtime approval|Breaks and handovers|Standby cover|Absence reporting|Peak week planning",
      [("shift-roster", [("week", "int:1:52"), ("site", "site"), ("shift", "choice:early/late/night/weekend"), ("team", "choice:Dock/Picking/Loading/Returns/Yard"), ("lead", "person"), ("planned_staff", "int:6:44"), ("confirmed", "int:4:44")]),
       ("swap-requests", [("request", "id:ROS"), ("requested_by", "person"), ("original_date", "date"), ("shift", "choice:early/late/night"), ("approved_by", "person"), ("status", "choice:approved/declined/pending")])]),
    T("supplier-onboarding", "Supplier Onboarding", "SUP",
      "supplier questionnaire|bank detail check|insurance certificate|code of conduct form|vendor master record|risk screening|"
      "reference check|onboarding call|framework agreement|sample order|contract schedule|invoice format|portal account|audit visit|data sheet",
      "procurement analyst|supplier manager|category buyer|finance controller|compliance officer|vendor administrator",
      "SupplierGate|VendorBook|OnboardTrack",
      "Request and screening|Documents required|Risk classification|Setting up the vendor record|Trial orders|Periodic review|Offboarding a supplier",
      [("supplier-register", [("vendor_no", "id:VND"), ("name", "supplier"), ("country", "choice:DE/BE/FR/SE/PL/GB/NL/IT"), ("risk_class", "choice:low/medium/high"),
                              ("onboarded", "date"), ("annual_spend_eur", "eur:2000:480000"), ("review_due", "date")]),
       ("onboarding-checklist", [("vendor_no", "id:VND"), ("document", "choice:insurance certificate/bank letter/code of conduct/risk questionnaire/data sheet"), ("received", "date"), ("checked_by", "person"), ("result", "choice:accepted/returned/waived")])]),
    T("warehouse-slotting", "Warehouse Slotting", "SLT",
      "pick face|reserve bay|slotting review|fast mover list|bay label|aisle map|replenishment trigger|pick path|"
      "putaway rule|slot swap|bulk zone|carton flow lane|mezzanine bay|cycle count|slot dimension sheet",
      "slotting analyst|warehouse planner|inventory controller|zone supervisor|operations engineer|picking team leader",
      "SlotMap|PickPath|BayBoard",
      "Slotting principles|Fast movers and pick faces|Bulk and reserve zones|Replenishment triggers|Bay labelling|Quarterly slotting review|Cycle counts",
      [("slot-assignments", [("slot", "id:SLT"), ("zone", "choice:A/B/C/D/Mezz/Bulk"), ("aisle", "int:1:40"), ("sku_group", "choice:small parts/cartons/fragile goods/long items/bulk"), ("picks_per_week", "int:3:900"), ("bay_width_mm", "int:600:1800"), ("last_review", "date")]),
       ("cycle-counts", [("count_id", "id:CYC"), ("site", "site"), ("zone", "choice:A/B/C/D/Mezz/Bulk"), ("counted", "date"), ("lines", "int:20:320"), ("variance_pct", "dec:0:3:2"), ("counter", "person")])]),
    T("pallet-standards", "Pallet and Load Carrier Standards", "PLT",
      "pallet grade|load carrier|pallet exchange|stretch wrap pattern|corner board|pallet inspection|height limit|"
      "label position|mixed pallet|half pallet|pallet pool account|repair bay|plastic pallet|collar frame|pallet cage",
      "pallet pool coordinator|quality inspector|packaging engineer|dock supervisor|warehouse planner|returns clerk",
      "PoolTrack|PalletCheck|LoadLabel",
      "Pallet grades|Wrapping and securing|Height and weight limits|Pallet exchange accounts|Inspection and repair|Labels and barcodes|Mixed pallets",
      [("pallet-exchange", [("note_no", "id:PLT"), ("partner", "choice:Skarsgaard Foods/Delmare Home/Ruthven Pharma/Pinewood Outdoor/Calder & Voss"), ("date", "date"), ("grade_a", "int:0:120"), ("grade_b", "int:0:60"), ("rejected", "int:0:14"), ("site", "site")]),
       ("pallet-inspections", [("inspection", "id:INS"), ("site", "site"), ("inspected", "date"), ("sample", "int:30:200"), ("defects", "int:0:19"), ("inspector", "person"), ("action", "choice:none/repair/replace/escalate")])]),
    T("returns-processing", "Returns Processing", "RET",
      "return authorisation|returns bay|grading sheet|refurbishment lane|disposition code|customer credit note|return slip|"
      "quarantine shelf|reshelving lane|carrier collection|photo record|serial number scan|resale channel|reason code|returns backlog",
      "returns supervisor|grading clerk|customer care lead|quality inspector|refurbishment technician|returns planner",
      "ReturnDesk|GradeIt|CreditPilot",
      "Authorising a return|Receiving and grading|Disposition|Credit notes|Refurbishment|Backlog management|Reporting",
      [("returns-log", [("return_id", "id:RET"), ("received", "date"), ("site", "site"), ("reason", "choice:wrong item/faulty/not needed/late/duplicate order"), ("grade", "choice:A/B/C/scrap"), ("disposition", "choice:resell/refurbish/return to supplier/recycle"), ("handling_min", "int:4:55")]),
       ("credit-notes", [("note", "id:CRN"), ("return_id", "id:RET"), ("issued", "date"), ("amount_eur", "eur:8:1900"), ("approver", "person"), ("status", "choice:issued/held/rejected")])]),
    T("fuel-card-reconciliation", "Fuel Card Reconciliation", "FCR",
      "fuel card|monthly statement|pump transaction|odometer reading|card block|merchant mismatch|unmatched line|"
      "driver PIN|fuel type code|card limit|VAT breakdown|fleet card portal|refund claim|lost card report|bulk invoice",
      "fleet accountant|fuel card administrator|transport controller|accounts payable clerk|depot manager|fleet analyst",
      "CardMatch|FleetLedger|PumpCheck",
      "Monthly statement import|Matching rules|Unmatched transactions|Card limits and blocks|Lost or stolen cards|Month-end close|Supplier queries",
      [("fuel-transactions", [("txn", "id:FCR"), ("card_last4", "int:1000:9999"), ("date", "date"), ("litres", "dec:30:420:1"), ("amount_eur", "eur:40:620"), ("station", "choice:Shell/Total/Esso/Aral/Orlen/Circle K/Q8"), ("match", "choice:matched/unmatched/disputed")]),
       ("card-limits", [("card_last4", "int:1000:9999"), ("site", "site"), ("monthly_limit_eur", "eur:600:4000"), ("blocked", "choice:no/no/no/yes"), ("last_review", "date")])]),
    T("ev-charging-pilot", "EV Charging Pilot", "EVC",
      "charger bay|charging session|pilot depot|load balancing rule|RFID tag|charger firmware|grid connection|"
      "driver briefing|fault ticket|energy meter|off-peak window|van conversion|session log|downtime report|power cap",
      "pilot manager|charging operations lead|electrical engineer|fleet planner|energy analyst|depot manager",
      "ChargeView|VoltDesk|PilotLog",
      "Pilot scope|Installation|Charging rules for drivers|Load balancing|Faults and support|Data collection|Review criteria",
      [("charging-sessions", [("session", "id:EVC"), ("site", "site"), ("start", "date"), ("kwh", "dec:6:96:1"), ("duration_min", "int:18:420"), ("vehicle_type", "choice:van/box truck/pool car"), ("fault", "choice:none/none/none/timeout/rfid/cable")]),
       ("charger-uptime", [("charger", "id:CHG"), ("site", "site"), ("month", "int:1:12"), ("uptime_pct", "dec:91:100:2"), ("faults", "int:0:7"), ("firmware", "choice:2.1/2.2/2.4/3.0")])]),
    T("internal-audit", "Internal Audit Checklists", "AUD",
      "audit checklist|finding rating|corrective action|evidence sample|audit schedule|sign-off sheet|control test|"
      "follow-up review|management response|risk register entry|audit trail|walkthrough note|closing meeting|observation|re-test",
      "internal auditor|quality manager|audit coordinator|process owner|risk officer|site manager",
      "AuditTrail|ControlBook|FindingsHub",
      "Planning an audit|Fieldwork|Rating findings|Management responses|Follow-up|Reporting to the board|Audit independence",
      [("audit-findings", [("finding", "id:AUD"), ("site", "site"), ("area", "choice:receiving/dispatch/maintenance/finance/IT/facilities"), ("rating", "choice:minor/moderate/major"), ("raised", "date"), ("owner", "person"), ("due", "date"), ("status", "choice:open/in progress/closed")]),
       ("audit-schedule", [("audit", "id:AUD"), ("site", "site"), ("process", "choice:receiving/dispatch/maintenance/finance/IT/facilities"), ("planned", "date"), ("auditor", "person"), ("days", "int:1:6")])]),
    T("procurement-approvals", "Procurement Approval Matrix", "PRC",
      "purchase requisition|approval level|framework call-off|three-quote rule|budget holder|single-source request|"
      "purchase order|goods receipt|invoice match|delegated authority|tender|urgent purchase|capital request|price check|supplier shortlist",
      "budget holder|category buyer|procurement lead|finance controller|cost centre owner|purchasing assistant",
      "BuyDesk|ApprovalFlow|PO-Pilot",
      "Approval thresholds|Quotes and tenders|Single-source purchases|Urgent purchases|Goods receipt and invoice matching|Capital requests|Delegation",
      [("purchase-orders", [("po", "id:PRC"), ("category", "choice:packaging/IT/facilities/training/fleet parts/catering"), ("raised", "date"), ("value_eur", "eur:120:96000"), ("approver_level", "choice:L1/L2/L3/L4"), ("buyer", "person"), ("status", "choice:approved/open/received/invoiced")]),
       ("delegations", [("delegate", "person"), ("level", "choice:L1/L2/L3"), ("limit_eur", "eur:1000:60000"), ("valid_from", "date"), ("valid_to", "date"), ("cost_centre", "id:CC")])]),
    T("facilities-maintenance", "Facilities Maintenance", "FAC",
      "dock leveller|roller door|lighting circuit|heating unit|fire extinguisher check|roof inspection|floor marking|"
      "air handling filter|lift service|toilet block|gutter clearance|door closer|pump room|water meter|boiler service",
      "facilities manager|maintenance technician|building services engineer|site services manager|planner|health and safety adviser",
      "FixIt|BuildingLog|PlanMaint",
      "Planned maintenance|Reactive repairs|Inspection rounds|Lifts and doors|Heating and ventilation|Spare parts|Reporting a fault",
      [("maintenance-log", [("work_order", "id:FAC"), ("site", "site"), ("asset", "thing"), ("reported", "date"), ("priority", "choice:P1/P2/P3/P4"), ("hours", "dec:0.5:14:1"), ("cost_eur", "eur:0:2600"), ("status", "choice:done/open/waiting parts")]),
       ("inspection-rounds", [("round", "id:RND"), ("site", "site"), ("area", "choice:dock/office/yard/plant room/roof/canteen"), ("date", "date"), ("items_checked", "int:8:60"), ("defects", "int:0:6"), ("inspector", "person")])]),
    T("driver-licensing", "Driver Licensing Renewals", "DLC",
      "licence check|medical fitness renewal|CPC module|digital card download|photo update|category C entitlement|"
      "renewal reminder|licence copy|points endorsement|provisional entitlement|annual check|ADR card|licence scan|training day|reminder letter",
      "driver compliance officer|transport planner|HR coordinator|depot supervisor|training manager|fleet administrator",
      "LicenceWatch|DriverFile|CheckPoint",
      "Licence checks|Renewal reminders|Professional competence modules|Fitness renewals|Foreign licences|Records and retention|Non-renewal handling",
      [("licence-expiry", [("driver_no", "id:DLC"), ("site", "site"), ("category", "choice:B/C/CE/D"), ("expires", "date"), ("last_check", "date"), ("cpc_hours_done", "int:0:35"), ("status", "choice:valid/renewal due/expired")]),
       ("renewal-reminders", [("driver_no", "id:DLC"), ("reminder", "choice:90 days/60 days/30 days/overdue"), ("sent", "date"), ("acknowledged", "choice:yes/no"), ("agent", "person")])]),
    T("customer-complaints", "Customer Complaint Handling", "CMP",
      "complaint ticket|root cause note|goodwill gesture|escalation path|apology letter|service recovery plan|"
      "complaint category|response template|trend report|feedback survey|call recording|resolution code|return call request|compensation offer|case review",
      "customer care lead|complaints handler|quality manager|account manager|service recovery officer|team leader",
      "CareDesk|ComplaintLog|TrendView",
      "Logging a complaint|Acknowledgement and response times|Root cause analysis|Service recovery|Escalation|Trend reporting|Closing a case",
      [("complaint-log", [("case", "id:CMP"), ("received", "date"), ("customer", "customer"), ("category", "choice:late collection/billing/status information/service attitude/label problem"), ("severity", "choice:low/medium/high"), ("handler", "person"), ("days_to_close", "int:1:38")]),
       ("complaint-trends", [("month", "int:1:12"), ("site", "site"), ("opened", "int:2:84"), ("closed", "int:2:84"), ("avg_days", "dec:1:19:1"), ("repeat_pct", "dec:0:16:1")])]),
    T("closure-calendar", "Peak and Closure Calendars", "CAL",
      "closure date|peak week|cut-off time|bridge day notice|extra collection|seasonal schedule|holiday cover rota|"
      "customer notice|dispatch window|early cut-off|blackout date|reopening plan|volume forecast|calendar update|regional note",
      "operations planner|regional manager|customer care lead|dispatch supervisor|network planner|site manager",
      "CalendarHub|PeakPlan|NoticeBoard",
      "How the calendar is built|Regional closures|Peak weeks|Cut-off times|Customer notices|Cover rotas|Changes after publication",
      [("closure-dates", [("entry", "id:CAL"), ("region", "choice:Netherlands North/Rhine/Lyon area/Lombardy/Mazovia/Texas"), ("date", "date"), ("type", "choice:closure/early cut-off/extra collection"), ("notice_days", "int:7:60"), ("published", "date")]),
       ("peak-forecast", [("week", "int:36:52"), ("site", "site"), ("forecast_pallets", "int:800:9800"), ("extra_shifts", "int:0:14"), ("agency_slots", "int:0:60")])]),
    T("project-status", "Project Status Reports", "PRJ",
      "yard camera rollout|label printer refresh|dock scheduling upgrade|warehouse Wi-Fi survey|returns portal pilot|"
      "meeting room booking tool|phone system migration|forklift fleet review|archive digitisation|badge reader replacement|"
      "driver app update|training room refit|intranet search|rack labelling|scanner replacement",
      "project manager|programme lead|workstream owner|steering group chair|business analyst|test coordinator",
      "ProjectPulse|MilestoneBoard|RiskLog",
      "Summary and status|Milestones|Budget|Risks and issues|Dependencies|Decisions needed|Next period plan",
      [("project-milestones", [("milestone", "id:PRJ"), ("project", "thing"), ("owner", "person"), ("planned", "date"), ("forecast", "date"), ("rag", "choice:green/amber/red"), ("percent_done", "int:0:100")]),
       ("project-risks", [("risk", "id:RSK"), ("project", "thing"), ("probability", "choice:low/medium/high"), ("impact", "choice:low/medium/high"), ("owner", "person"), ("review", "date")])]),
    T("knowledge-base", "Knowledge Base How-tos", "KBA",
      "printer queue|shared mailbox|meeting room screen|label template|scanner pairing|password manager|badge reader|"
      "spreadsheet macro|VPN profile|desk phone|conference bridge|file share|label roll change|barcode gun cradle|intranet page",
      "service desk agent|knowledge editor|IT trainer|site IT contact|helpdesk lead|application owner",
      "HelpCentre|KBase|DeskGuide",
      "Getting started|Printing and labels|Scanners and handhelds|Mailboxes and calendars|Meeting rooms|Remote access|Troubleshooting",
      [("kb-articles", [("article", "id:KBA"), ("topic", "thing"), ("owner", "person"), ("updated", "date"), ("views_90d", "int:0:2400"), ("helpful_pct", "int:40:99"), ("status", "choice:current/review due/archived")]),
       ("kb-feedback", [("article", "id:KBA"), ("received", "date"), ("rating", "int:1:5"), ("comment_code", "choice:unclear/outdated/helpful/missing screenshot/typo")])]),
    T("consumables-prices", "Consumables Price Lists", "CON",
      "bubble wrap roll|edge protector|void-fill paper|label ribbon|shrink hood|pallet label|safety knife|"
      "cable tie pack|dock seal repair kit|gloves, pair|marker pen|adhesive tape dispenser|archive box|document wallet|toner cartridge",
      "category buyer|purchasing assistant|packaging engineer|supplier manager|budget holder|warehouse planner",
      "PriceBook|CatalogLink|BuyDesk",
      "How to read the price list|Price validity|Minimum order quantities|Delivery terms|Discount tiers|Substitutions|Price changes",
      [("consumables-catalogue", [("item_code", "id:NWC"), ("description", "thing"), ("unit", "choice:each/pack of 10/pack of 50/roll/box of 100"), ("price_eur", "dec:0.8:96:2"), ("min_qty", "int:1:60"), ("lead_days", "int:1:12"), ("valid_from", "date")]),
       ("price-changes", [("item_code", "id:NWC"), ("old_price_eur", "dec:0.8:96:2"), ("new_price_eur", "dec:0.8:96:2"), ("effective", "date"), ("reason", "choice:supplier increase/volume tier/new supplier/correction")])]),
    T("sensor-calibration", "Sensor Calibration Records", "CAB",
      "reference thermometer|probe certificate|ice-point check|data logger|humidity sensor|scale verification|"
      "pressure gauge|calibration label|drift report|tolerance band|sensor swap|handheld scanner test|weighbridge check|calibration interval|out-of-tolerance note",
      "calibration technician|quality inspector|metrology lead|maintenance planner|site quality manager|equipment owner",
      "CalTrack|MetroLog|LoggerHub",
      "Calibration intervals|Reference standards|Recording results|Out-of-tolerance handling|Labels and certificates|External laboratories|Data logger handling",
      [("calibration-records", [("record", "id:CAB"), ("instrument", "choice:probe/logger/scale/gauge/humidity sensor"), ("site", "site"), ("calibrated", "date"), ("deviation", "dec:0.01:0.9:2"), ("tolerance", "dec:0.1:1.0:1"), ("result", "choice:pass/pass/pass/adjusted/fail")]),
       ("logger-readings", [("logger", "id:LOG"), ("site", "site"), ("date", "date"), ("min_c", "dec:1:6:1"), ("max_c", "dec:6:9:1"), ("alarms", "int:0:3")])]),
    T("packaging-waste", "Packaging Waste Handling", "WST",
      "cardboard baler|waste bin colour|pallet wood collection|film recycling|hazardous bin|skip swap|"
      "waste log|weighing ticket|shredder bin|toner return|bulky item pickup|segregation sign|waste carrier note|crushing cycle|collection schedule",
      "waste coordinator|facilities manager|yard supervisor|environmental officer|site services manager|housekeeping lead",
      "WasteLog|BinTrack|SkipBook",
      "Segregation|Balers and compactors|Collections|Weighing and records|Hazardous items|Signage|Spill response",
      [("waste-collections", [("ticket", "id:WST"), ("site", "site"), ("date", "date"), ("stream", "choice:cardboard/film/wood/mixed/metal/paper"), ("weight_kg", "int:80:6400"), ("carrier", "choice:Verhoeven/Brandt Entsorgung/Maes/Duval Recyclage"), ("cost_eur", "eur:0:640")]),
       ("baler-cycles", [("baler", "id:BAL"), ("site", "site"), ("week", "int:1:52"), ("bales", "int:4:90"), ("avg_bale_kg", "int:280:520"), ("jams", "int:0:4")])]),
    T("visitor-access", "Visitor Registration", "VIS",
      "visitor badge|pre-registration|escort rule|gate log|day pass|sign-in tablet|host notification|vehicle check|"
      "delivery driver briefing|restricted area map|badge return|group visit|lost badge|photo check|evening access request",
      "reception lead|security desk officer|site host|gate supervisor|facilities planner|site services manager",
      "GateDesk|VisitBook|BadgeLog",
      "Pre-registration|Arrival and sign-in|Escorting|Restricted areas|Delivery drivers|Group visits|Lost badges",
      [("visitor-log", [("visit", "id:VIS"), ("site", "site"), ("date", "date"), ("host", "person"), ("visitors", "int:1:14"), ("arrived", "clock"), ("left", "clock"), ("badge_returned", "choice:yes/yes/yes/no")])]),
    T("mailroom-lost-property", "Mail Room and Lost Property", "MRM",
      "incoming letter|courier parcel|registered post item|lost property box|forwarding address|mail pigeonhole|franking machine|"
      "postage account|found item tag|unclaimed item|internal envelope|stamp reserve|sort table|collection slip|pickup rota",
      "mail room clerk|reception lead|office manager|site services manager|postal coordinator|facilities planner",
      "MailTrack|FoundIt|PostBook",
      "Receiving mail|Internal distribution|Outgoing post|Franking and postage accounts|Lost property|Unclaimed items|Holiday cover",
      [("lost-property", [("item", "id:MRM"), ("found", "date"), ("site", "site"), ("description", "choice:umbrella/jacket/keys/phone/bottle/glasses/charger/bag"), ("location", "choice:canteen/dock/office/yard/meeting room"), ("claimed", "choice:yes/no"), ("disposed", "choice:yes/no")])]),
    T("workshop-tyres", "Workshop and Tyre Management", "WSH",
      "tyre rotation|wheel balance|brake pad check|workshop bay slot|mobile fitter visit|tread depth reading|puncture repair|"
      "alignment check|spare wheel|axle grease|oil change|workshop booking|torque record|retread order|inspection pit",
      "workshop manager|fleet technician|tyre coordinator|fleet planner|depot manager|parts buyer",
      "WorkshopBook|TyreTrack|PitLog",
      "Workshop booking|Tyre checks|Rotation and replacement|Brakes and axles|Mobile fitters|Records|Parts ordering",
      [("tyre-checks", [("check", "id:WSH"), ("vehicle", "id:NWX"), ("date", "date"), ("tread_mm", "dec:2:14:1"), ("pressure_bar", "dec:6:9:1"), ("action", "choice:none/rotate/replace/repair"), ("mechanic", "person")])]),
    T("equipment-inspection", "Handling Equipment Inspection", "EQP",
      "pallet truck check|reach truck daily check|battery charger inspection|dock plate test|safety beam sensor|horn and light test|"
      "fork wear gauge|hydraulic hose check|tag-out label|operator pre-use form|scissor lift service|ladder check|strap tester|trolley wheel|lifting chain",
      "equipment supervisor|workshop manager|safety adviser|operator trainer|dock lead|maintenance planner",
      "EquipCheck|PreUse|TagBoard",
      "Daily checks|Weekly inspections|Annual examinations|Defect reporting|Tagging out equipment|Operator responsibilities|Records",
      [("equipment-checks", [("check", "id:EQP"), ("site", "site"), ("equipment", "choice:pallet truck/reach truck/scissor lift/dock plate/ladder"), ("date", "date"), ("result", "choice:pass/pass/pass/defect"), ("inspector", "person"), ("minutes", "int:3:35")])]),
    T("internal-comms", "Internal Communications", "COM",
      "staff newsletter|town hall slot|noticeboard post|intranet banner|manager briefing pack|pulse survey|recognition card|"
      "site bulletin|translation request|photo release|all-hands recording|poster template|quiz night|suggestion box|anniversary message",
      "communications lead|content editor|HR coordinator|site manager|events coordinator|intranet owner",
      "NewsDesk|PulseCheck|PostBoard",
      "Editorial calendar|Approval of messages|Translations|Pulse surveys|Site bulletins|Recognition|Crisis messages",
      [("pulse-survey", [("wave", "id:COM"), ("site", "site"), ("sent", "int:60:900"), ("responses", "int:20:700"), ("score", "dec:5.5:9.2:1"), ("comments", "int:0:130")])]),
    T("data-quality", "Master Data Quality", "MDQ",
      "address cleanse|duplicate customer record|postcode rule|unit-of-measure table|weight tolerance|barcode check digit|"
      "field validation|merge request|data steward queue|golden record|reference table|import template|rejection report|code list|sync job",
      "data steward|master data lead|business analyst|systems analyst|process owner|integration engineer",
      "DataGate|StewardQueue|CleanRoom",
      "Ownership of data|Validation rules|Duplicates and merges|Reference tables|Imports and syncs|Rejection handling|Quality metrics",
      [("dq-issues", [("issue", "id:MDQ"), ("table", "choice:customers/addresses/items/carriers/sites"), ("detected", "date"), ("records", "int:1:900"), ("rule", "choice:postcode/duplicate/missing field/unit mismatch/format"), ("steward", "person"), ("state", "choice:open/fixed/rejected")])]),
    T("carrier-subcontracting", "Carrier Subcontracting", "CRR",
      "lane tender|subcarrier scorecard|spot booking|rate sheet|proof pack|capacity call|carrier insurance document|"
      "tender round|lane award|booking confirmation|load tender email|carrier portal login|performance review|rate adjustment|blacklist entry",
      "transport buyer|lane manager|carrier relations lead|dispatch supervisor|network planner|finance controller",
      "LaneBook|CarrierView|TenderDesk",
      "Tender rounds|Scorecards|Spot bookings|Insurance and documents|Rate changes|Performance reviews|Suspending a carrier",
      [("carrier-scorecard", [("carrier", "supplier"), ("quarter", "choice:Q1 2026/Q2 2026/Q3 2026"), ("loads", "int:10:900"), ("acceptance_pct", "dec:60:100:1"), ("claims", "int:0:9"), ("rating", "choice:A/B/C")])]),
    T("energy-metering", "Site Energy Metering", "ENM",
      "sub-meter reading|monthly consumption sheet|lighting schedule|dock heater timer|compressed air check|meter swap|"
      "billing estimate|peak demand alert|solar panel log|boiler setpoint|night setback|reading round|tenant split|rate review|utility invoice",
      "energy analyst|facilities manager|building services engineer|finance controller|site services manager|meter reader",
      "MeterHub|UtilityLedger|ReadRound",
      "Meter reading rounds|Estimated bills|Peak demand|Lighting and heating schedules|Invoice checks|Meter faults|Reporting",
      [("meter-readings", [("meter", "id:ENM"), ("site", "site"), ("read_on", "date"), ("kwh", "int:800:96000"), ("previous", "int:800:96000"), ("read_by", "person"), ("flag", "choice:ok/ok/ok/check/estimated")])]),
]

assert len({t["slug"] for t in TOPICS}) == len(TOPICS)
