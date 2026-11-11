Feature: Verify the external mall consumer against the real HealthFlow service
  Rule: The browser must exercise the HTTP boundary
    Scenario: Enter, consent, read and revoke
      Given an isolated HealthFlow database with synthetic mall subjects and reports
      And the built mall consumer is served from another loopback origin
      When the customer exchanges a ticket, grants consent, reads a report and withdraws consent
      Then the API records the decisions and denies access to deleted reports
      And the browser displays the report and original image without script errors
    Scenario: Confirm observations and follow a published card into goods
      Given deterministic evidence and goods services at the external HTTP seams
      When the customer explicitly confirms the abnormal observations
      Then hidden observations stay pending
      And published card text, source links and recommendation consent behave as specified
  Rule: Report upgrades preserve facts and optional choices
    Scenario: An open boundary cannot become normal in evidence matching
      Given a confirmed value exactly on an excluded reference endpoint
      When the report is assessed through the evidence API
      Then the observation is explicitly withheld and remains visibly abnormal
      And the summary does not say all observations are normal
    Scenario: Empty evidence is not a clean bill of health
      Given abnormal observations without published findings
      When the report is opened at mobile widths 360 and 390
      Then the server summary is shown without an invented healthy conclusion
      And expanding an indicator shows the source and server-parsed range
    Scenario: Optional answers survive failures and concurrent writes
      Given a report with no lifestyle answers
      When the user answers by keyboard, saves, reloads, skips and answers again
      Then saved answers are restored with their source labels
      And a failed submission preserves the draft and base report
      And conflicting writes from the same revision have only one winner
      And withdrawal deletes the answers with the report
