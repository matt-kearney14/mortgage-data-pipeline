# Questions for the professor

*2026-10-08. Every page characteristic in the dataset now has a value. Where your
coding sheet (`beta_coding`) had a value, it is used unchanged. Where it was blank,
we coded the page from its content, following the pattern in your own coding,
and labelled every such cell `coded_by_us`. Each page and each value is in
`output/dictionary_review_for_professor.xlsx` (sheet `Page_templates`; your values
are marked "(professor)"). Any correction is a one-cell edit to
`docs/page_template_coding.csv` and a re-run.*

## 1. Decisions only you can make

1. **ProcessRelated (your "????").** We kept your values exactly. For the pages you
   did not code, we copied the value of your coded page of the same kind (e.g. the
   VA and ARM loan modules follow your FHA and conventional ones). It is not the
   union of Borrower- and Lender-process: 28,672 pageviews are one of those but not
   ProcessRelated. What should ProcessRelated mean?
2. **Audio as a count.** Your `coding_dictionary` defines Audio as a count of audio
   links on the page; your sheet codes it 0/1. We now count the distinct clips that
   play on each page (e.g. Application Documents Explained 23, Closing Documents
   Overview 28, each Taxes & Insurance FAQ 6). Eight pages you coded Audio 1 have no
   clips played from them at all — the LE and CD pages, the FHA and conventional
   loan modules, People & Process, How Your Rate Was Determined, Budgeting Basics,
   and the VA fact sheet — so they read 0. Did your 1 there mean the video's
   narration? Your 0/1 is kept alongside as `Audio_professor`.

## 2. Places where your own coding differs from its pattern

3. **GeneralFinancial.** Pages about the borrower's own loan are never
   GeneralFinancial in your coding; money-skills pages (budgeting, credit, rates) are.
   Two exceptions: the **Budgeting FAQs** (0) and the **"What factors affect rates"
   infographic** (0). We kept both. Should they be 1?
4. **Video on the VA fact sheet.** You coded Video 1 on
   `/JustTheFacts/your-va-fixed-rate-loan-just-the-facts`. A recording of the demo
   site shows videos only on the /Module/ pages, and the other fact sheets are 0.
   We kept your 1. Is it intended?

## 3. Please confirm what we coded

5. **Borrower- / Lender-process** on the pages you left blank. The CD page follows
   the LE page (Borrower 1, Lender 0); FAQ and module pages follow your coding of
   their own audio clips where you coded them. Pages most open to judgement: the
   loan-type modules and loan-payment slideshows (we coded Borrower 1 and 0
   respectively).
6. **LoanTermsRelated** — you coded it only on audio clips. We coded pages using the
   Loan Estimate's own "Loan Terms" section: interest rate, loan amount, monthly
   payment. So the loan-type modules, loan-payment slideshows, fact sheets, rate
   pages, LE and CD pages are 1; taxes & insurance and mortgage-insurance FAQs are 0.
7. **Goal_to_inform / Goal_to_Advise** — every page you coded is Goal_to_inform 1,
   so every content page is; Goal_to_Advise is 1 where the page, or your coding of
   its clips, recommends action (credit management, People & Process, budgeting
   FAQs). Navigation pages and downloads have no goal.
8. **The Dashboard** was treated as a navigation page. It shows the borrower's own
   Loan Estimate and lists their documents, so we now code it as the borrower's
   own loan: Personalized, LoanEstimateRelated and LEDocument 1, and CDRelated /
   CDDocument 1 once the borrower has a Closing Disclosure.
9. **Download on a page** means the page offers a document download, as your 1 on
   the LE and CD pages suggests; so the Dashboard and Application Documents
   Explained are 1.
10. **Downloads.** The log does not say what a downloaded document is. We type each
    download from the page it was clicked from (LE page, CD page, or the Service
    Provider List on Application Documents Explained), then from the same document
    downloaded elsewhere, then from document number order (the LE's number is lower
    than the CD's). 95% are typed; the dataset records which evidence was used. A
    LoanDocument id → type table from the data owner would replace this.
