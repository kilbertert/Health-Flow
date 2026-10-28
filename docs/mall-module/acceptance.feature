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
