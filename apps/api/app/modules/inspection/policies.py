"""Synthetic policy documents for the demo. Fictional text written for the
Imaging Ops Copilot demo; not clinical or legal guidance. Thresholds are the
same placeholders the apps use (and are labelled as such)."""

# id, title, owner, version, effective (days ago), change note, [(heading, text)]
POLICIES = [
    ("POL-CONTRAST", "Contrast media administration", "Medical director", "2.0", 120,
     "eGFR validity window shortened from 180 to 90 days for patients with risk factors.", [
         ("Scope", "This policy applies to every intravenous iodinated and gadolinium-based contrast injection at all sites. "
                   "Only technologists and nurses who have completed contrast and IV training may inject contrast."),
         ("Kidney function screening", "Patients with kidney disease, diabetes, hypertension on treatment or age over 60 need an "
                                       "eGFR result taken within the last 90 days before a contrast exam. Patients without risk "
                                       "factors do not need a routine eGFR."),
         ("eGFR thresholds", "If the eGFR is below 30 mL/min/1.73m2 the radiologist must review the request before contrast is "
                             "given. If the eGFR is between 30 and 44 the technologist confirms hydration instructions were "
                             "given. These thresholds are demo placeholders set by the medical director."),
         ("Prior contrast reactions", "A previous mild reaction (nausea, a few hives) needs a radiologist note in the requisition. "
                                      "A previous moderate or severe reaction requires premedication as ordered by the radiologist "
                                      "or a non-contrast alternative. Premedication is documented in the exam record."),
         ("Observation after injection", "Outpatients stay in the department for 15 minutes after a contrast injection. Patients with "
                                         "a previous reaction stay for 30 minutes. The emergency cart is checked daily."),
         ("Extravasation", "If contrast leaks into the tissue, stop the injection, elevate the limb, apply a cold compress and inform "
                           "the radiologist. Volumes over 50 mL are reported as an incident and the patient is called the next day."),
     ]),
    ("POL-MRI", "MRI safety and access zones", "MRI safety officer", "1.3", 300, "", [
        ("Zones", "The MRI suite is divided into four zones. Zone I is public, Zone II is the screening and waiting area, Zone III is "
                  "the control room and Zone IV is the magnet room. Zone III and IV doors stay locked."),
        ("Screening before Zone IV", "Every patient and every visitor completes the MRI safety questionnaire and is screened by an "
                                     "MRI technologist before entering Zone IV. A flagged answer must be reviewed and cleared by a "
                                     "technologist or radiologist; the system never clears a patient automatically."),
        ("Implants and devices", "Implants are scanned only when the exact model is verified as MR Conditional and the conditions "
                                 "(field strength, SAR limits) can be met. Pacemakers and defibrillators require cardiology "
                                 "programming support on site. Unknown implants are not scanned until identified."),
        ("Metal fragments", "Patients with a history of metal in the eyes need an orbit X-ray read as clear before MRI."),
        ("Pregnancy", "MRI in pregnancy is allowed when the radiologist confirms the benefit. Gadolinium is avoided in pregnancy "
                      "unless the radiologist documents that it is essential."),
        ("Emergencies in the magnet room", "In a medical emergency the patient is moved out of Zone IV before resuscitation. The "
                                           "magnet is quenched only for a person pinned by a ferromagnetic object or a fire."),
    ]),
    ("POL-PRIVACY", "Privacy and access to personal health information", "Privacy officer", "3.1", 200,
     "Added monthly access monitoring and the 24-hour internal breach report.", [
        ("Principle", "Staff access personal health information only when they need it to provide or support care for that patient, "
                      "and only the minimum necessary. Being able to open a record is not permission to open it."),
        ("Own and family records", "Staff must not look up their own record or the records of family members, friends or "
                                   "colleagues through work systems. Staff who want their own results use the patient portal or "
                                   "ask the front desk like any other patient."),
        ("Access monitoring", "The privacy officer reviews access alerts every month, including access to patients not seen at the "
                              "user's site, after-hours access, bulk access, same-family-name access and self access. Each alert "
                              "is investigated and the outcome is documented."),
        ("Reporting a breach", "Any staff member who suspects a privacy breach reports it to the privacy officer within 24 hours. The "
                               "privacy officer decides whether the patient and the Information and Privacy Commissioner must be "
                               "notified, as required by PHIPA."),
        ("Consequences", "Unauthorized access leads to coaching, removal of access, discipline up to termination, and reporting to "
                         "the regulatory college where required."),
        ("Retention of audit logs", "Access logs are kept for at least 10 years and are protected against modification."),
    ]),
    ("POL-CRITICAL", "Communication of critical and urgent results", "Medical director", "1.2", 400, "", [
        ("Levels", "Level 1 critical findings (for example pulmonary embolism, tension pneumothorax) are communicated to the "
                   "ordering physician by phone within 60 minutes. Level 2 urgent findings within 24 hours. Level 3 significant "
                   "unexpected findings within 7 days."),
        ("Read-back", "Phone communication of a Level 1 finding uses read-back: the receiving clinician repeats the finding and the "
                      "radiologist confirms it."),
        ("Escalation", "If the ordering physician cannot be reached within the time limit, the case is escalated to the covering "
                       "physician and then to the medical director, who ensures the patient is contacted."),
        ("Documentation", "Every communication records who was informed, when, by what method and who acknowledged it. A case cannot "
                          "be closed without an acknowledgement."),
    ]),
    ("POL-RADIATION", "Radiation protection and CT dose management", "Radiation safety officer", "2.2", 150, "", [
        ("Reference levels", "Each CT protocol has a local diagnostic reference level for CTDIvol and DLP. The medical director and "
                             "the medical physicist review the reference levels every year."),
        ("Dose exceedances", "Every CT exam above its reference level is reviewed by a technologist or radiologist within 14 days. "
                             "The review records whether the dose was justified (for example patient size), a technique issue or "
                             "a protocol problem."),
        ("Pregnancy check", "Patients of childbearing age are asked about pregnancy before CT and X-ray of the abdomen or pelvis; "
                            "the answer is recorded."),
        ("Staff dosimetry", "Staff working with radiation wear dosimeters, which are read every quarter. Readings above the "
                            "investigation level are reviewed by the radiation safety officer."),
        ("Equipment testing", "Each CT and X-ray unit has an acceptance test before first use and an annual performance test by a "
                              "medical physicist."),
    ]),
    ("POL-ID", "Patient identification", "Operations manager", "1.0", 700, "", [
        ("Two identifiers", "Staff confirm two identifiers (full name and date of birth) before every exam, injection and release of "
                            "results. The room number or appointment time is never used as an identifier."),
        ("Time-out", "Before a contrast injection or an interventional procedure the team does a time-out: patient, exam, side and "
                     "allergies are confirmed aloud."),
        ("Wrong-patient events", "An exam done on the wrong patient is reported as an incident immediately and the medical "
                                 "director is informed."),
    ]),
    ("POL-INFECTION", "Infection prevention and control", "Operations manager", "1.4", 260, "", [
        ("Hand hygiene", "Staff clean their hands before and after every patient contact and after removing gloves."),
        ("Cleaning between patients", "Tables, coils, positioning aids and keyboards in patient areas are wiped with an approved "
                                      "disinfectant after every patient."),
        ("Ultrasound probes", "Endocavity ultrasound probes receive high-level disinfection after each use and the cycle is logged. "
                              "Probe covers are single use."),
        ("Isolation precautions", "Patients on contact or droplet precautions are booked at the end of the day where possible, and "
                                  "the room is cleaned before the next patient."),
    ]),
    ("POL-QA", "Peer review and quality assurance", "QA lead", "1.1", 330, "", [
        ("Sampling", "Each night a random sample of signed reports (5% by default) is assigned for blinded peer review. A report is "
                     "never assigned to the radiologist who signed it."),
        ("Scoring", "Reviewers grade each report as concur, minor discrepancy or significant discrepancy and record the type of "
                    "discrepancy."),
        ("Follow-up", "Significant discrepancies go to the QA committee within 30 days. If patient care may be affected, the "
                      "original radiologist issues an addendum and the referrer is informed."),
        ("Reports", "The QA lead reviews discrepancy rates by radiologist and exam type every month. QA records are kept for two "
                    "years and are confidential."),
    ]),
    ("POL-EQUIPMENT", "Equipment quality control and maintenance", "Operations manager", "1.0", 500, "", [
        ("Daily QC", "Technologists run the manufacturer's daily quality control on each scanner before the first patient and "
                     "record the result. A failed daily QC takes the scanner out of service until it passes."),
        ("Preventive maintenance", "Each scanner receives preventive maintenance at least every six months, or more often if the "
                                   "manufacturer requires it. Overdue maintenance is escalated to the operations manager."),
        ("Annual testing", "A medical physicist tests each CT, X-ray and MRI unit every year. Reports are filed in the equipment "
                           "record and deficiencies are corrected before the next test."),
        ("Out of service", "A faulty scanner is tagged out of service, its appointments are moved and the repair is recorded in the "
                           "equipment record."),
    ]),
]
