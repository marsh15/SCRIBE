# Runbook: Deploying the Dispatch Platform

Owner: Priya Raman, CTO. Applies to platform release v4.x.

## 1. Deploy windows

⟦Standard deployments are allowed on Tuesdays and Thursdays between 10:00 and 15:00 CET.⟧ ⟦No deployments are allowed between 15 December and 5 January.⟧

### 1.1 Exceptions

⟦Hotfixes may be deployed outside the windows with written approval from the CTO.⟧

## 2. Pre-deploy checklist

1. Confirm that the release candidate passed all tests in the pipeline.
2. Confirm that the on-call engineer is available for the next 2 hours.
3. Post a notice in the channel #ops-deploys.

## 3. Deployment steps

```bash
git tag v4.3.1
make release ENV=prod
⟦make canary PERCENT=5 DURATION=30m⟧
make promote
```

### 3.1 Canary

⟦The canary receives 5% of traffic for 30 minutes before full promotion.⟧

### 3.2 Health checks

⟦The health endpoint is /healthz and must return HTTP 200 within 2 seconds.⟧

## 4. Rollback

⟦Rollback must start within 15 minutes if the error rate exceeds 2% during the canary.⟧ Run `make rollback VERSION=<previous>`.

### 4.1 Database migrations

Migrations must be backward compatible for one release. ⟦Destructive migrations require a separate change ticket and are never deployed together with application code.⟧

## 5. After the deploy

Update the changelog and close the related INV tickets. The deploy owner posts a summary within 1 hour.
