# Email Delivery Platform — System Architecture

> Production architecture for a multi-tenant email campaign and delivery platform with Admin Panel, User Panel, distributed Workers, SMTP/Email Provider management, queue-based delivery, monitoring, reporting, and scalable infrastructure.

---

## 1. Project Overview

This project is a centralized email delivery management platform.

The system has three primary layers:

1. **Admin Control Plane**
2. **User Application**
3. **Distributed Email Worker Infrastructure**

The Admin controls campaigns, users, SMTP/provider resources, worker nodes, quotas, assignments, monitoring, and reports.

Users receive assigned campaigns/jobs and can configure permitted sending parameters such as batch size and approved sending resources.

Workers execute delivery jobs independently from the Admin server.

### Core Pipeline

```text
Campaign
    ↓
Recipient Validation
    ↓
Queue
    ↓
Batch Creation
    ↓
Worker Assignment
    ↓
Provider/SMTP
    ↓
Delivery
    ↓
Event Processing
    ↓
Reports
```

---

# 2. Primary Goals

The platform must provide:

* Multi-user support
* Multi-tenant architecture
* Admin-controlled campaigns
* User-specific campaign access
* SMTP/provider pool management
* SMTP/provider assignment
* Worker/VPS management
* Queue-based sending
* Batch splitting
* Retry handling
* Delivery tracking
* Bounce handling
* Complaint handling
* Unsubscribe/suppression handling
* Worker health monitoring
* Provider health monitoring
* User quotas
* Campaign reports
* Real-time monitoring
* Audit logging
* Horizontal scaling

---

# 3. Important Safety and Compliance Requirements

The platform is intended for legitimate, permission-based email communication.

The system must support:

* Consent-based recipient lists
* Unsubscribe handling
* Suppression lists
* Bounce suppression
* Complaint suppression
* Sender authentication
* SPF/DKIM/DMARC configuration
* Provider sending policies
* Audit logs
* Abuse prevention

The architecture must not depend on:

* IP rotation to evade provider limits
* Provider-limit bypassing
* Spam filtering bypasses
* Unauthorized recipient harvesting
* Sending to suppressed recipients

---

# 4. High-Level Architecture

```text
                         ┌─────────────────────────┐
                         │       ADMIN PANEL       │
                         │                         │
                         │ Users                   │
                         │ Campaigns               │
                         │ SMTP / Providers        │
                         │ Workers                 │
                         │ Reports                 │
                         │ System Settings         │
                         └────────────┬────────────┘
                                      │
                                      ▼
                         ┌─────────────────────────┐
                         │        ADMIN API        │
                         │                         │
                         │ Authentication          │
                         │ Authorization           │
                         │ Campaign Management     │
                         │ User Management         │
                         │ Provider Management     │
                         │ Reporting               │
                         └────────────┬────────────┘
                                      │
                   ┌──────────────────┴──────────────────┐
                   │                                     │
                   ▼                                     ▼
          ┌───────────────────┐               ┌───────────────────┐
          │    PostgreSQL     │               │ Queue / Redis /   │
          │                   │               │ Message Broker    │
          │ Users             │               │                   │
          │ Campaigns         │               │ Jobs              │
          │ Providers         │               │ Rate Limits       │
          │ Reports           │               │ Locks             │
          │ Audit Logs        │               │ Worker State      │
          └───────────────────┘               └─────────┬─────────┘
                                                        │
                           ┌────────────────────────────┼───────────────────────────┐
                           │                            │                           │
                           ▼                            ▼                           ▼
                    ┌──────────────┐             ┌──────────────┐             ┌──────────────┐
                    │ Worker VPS 1 │             │ Worker VPS 2 │             │ Worker VPS N │
                    │              │             │              │             │              │
                    │ Sender       │             │ Sender       │             │ Sender       │
                    │ Rate Limit   │             │ Rate Limit   │             │ Rate Limit   │
                    │ Retry        │             │ Retry        │             │ Retry        │
                    └──────┬───────┘             └──────┬───────┘             └──────┬───────┘
                           │                            │                            │
                           └────────────────┬───────────┴────────────────────────────┘
                                            │
                                            ▼
                                  ┌────────────────────┐
                                  │ Approved Email     │
                                  │ Providers / SMTP   │
                                  └─────────┬──────────┘
                                            │
                                            ▼
                                      Recipients
                                            │
                                            ▼
                                  Delivery Events
                                            │
                                            ▼
                                  Event Processor
                                            │
                                            ▼
                                       Database
```

---

# 5. Architectural Principles

## 5.1 Control Plane vs Data Plane

The system must separate control and execution.

### Control Plane

Responsible for:

* Users
* Campaigns
* Provider configuration
* Worker registration
* Assignments
* Quotas
* Policies
* Reporting
* Administration

### Data Plane

Responsible for:

* Fetching jobs
* Processing batches
* Sending messages
* Retry handling
* Reporting delivery results

---

# 6. Components

## 6.1 Admin Panel

The Admin Panel is the primary control interface.

### Main sections

```text
Dashboard

Users
 ├── All Users
 ├── Active
 ├── Suspended
 └── Limits

Campaigns
 ├── All
 ├── Pending
 ├── Processing
 ├── Completed
 └── Failed

Providers
 ├── SMTP / Email Providers
 ├── Assignments
 ├── Health
 └── Disabled

Workers
 ├── Nodes
 ├── Online
 ├── Offline
 ├── Capacity
 └── Health

Queues
 ├── Pending
 ├── Processing
 ├── Failed
 └── Retry

Reports
 ├── Delivery
 ├── Bounce
 ├── Complaint
 └── User Reports

System
 ├── Settings
 ├── API Keys
 ├── Audit Logs
 └── Security
```

---

# 7. User Panel

The User Panel must remain intentionally simple.

## Dashboard

```text
Today's Sending

Assigned       100,000
Processed       72,430
Delivered       68,901
Failed           3,529
Remaining       27,570

Current Speed
410/sec

[ Start Sending ]
```

## Campaign Screen

```text
Campaign

Campaign Name
Subject
From Name
From Email

Recipient File

Batch Size
[500]
[1000]
[5000]

Provider
[Select]

[START]
```

## Sending Monitor

```text
Campaign #1024

████████████████░░░░

72%

Processed      72,430
Delivered      68,901
Failed          3,529
Remaining      27,570

Speed           410/sec

[Pause]
[Stop]
```

---

# 8. Worker Infrastructure

Workers are independent execution nodes.

A worker can run on:

* VPS
* Cloud VM
* Dedicated server
* Container cluster

Each worker communicates with the Control API and Queue.

## Worker lifecycle

```text
START
 ↓
Load configuration
 ↓
Authenticate
 ↓
Register
 ↓
Heartbeat
 ↓
Fetch job
 ↓
Claim job
 ↓
Process batch
 ↓
Send
 ↓
Report result
 ↓
Fetch next job
```

---

# 9. Worker Registration

Every worker must have a unique identity.

Example:

```json
{
  "worker_id": "worker-001",
  "name": "Dhaka Worker 01",
  "version": "1.0.0",
  "capacity": 80,
  "status": "online"
}
```

The worker receives a secure credential during provisioning.

Worker credentials must not be hard-coded in source code.

---

# 10. Worker Heartbeat

Every worker sends periodic heartbeat information.

```json
{
  "worker_id": "worker-001",
  "status": "online",
  "cpu": 42,
  "memory": 61,
  "active_jobs": 3,
  "current_rate": 65,
  "timestamp": "2026-09-29T10:00:00Z"
}
```

If heartbeat is missing for the configured threshold:

```text
ONLINE
   ↓
WARNING
   ↓
OFFLINE
```

The Admin Panel must display worker health.

---

# 11. Queue Architecture

Email delivery must be queue-based.

Do not send large campaigns directly from the API request.

### Incorrect

```text
HTTP Request
    ↓
Send 1,000,000 emails
    ↓
HTTP Response
```

### Correct

```text
HTTP Request
    ↓
Create Campaign
    ↓
Create Jobs
    ↓
Queue
    ↓
Workers
```

---

# 12. Job Architecture

Example job:

```json
{
  "job_id": "JOB-92831",
  "campaign_id": "CAMP-1001",
  "user_id": "USR-102",
  "provider_id": "SMTP-12",
  "batch_size": 1000,
  "status": "pending"
}
```

## Job lifecycle

```text
pending
   ↓
claimed
   ↓
processing
   ↓
completed
```

Failure:

```text
processing
   ↓
failed
   ↓
retry
```

Permanent failure:

```text
retry
   ↓
dead-letter
```

---

# 13. Batch Processing

A campaign may contain:

```text
1,000,000 recipients
```

The user may choose:

```text
Batch Size = 1,000
```

The system creates:

```text
Job 001 → 1,000
Job 002 → 1,000
Job 003 → 1,000
...
Job 1000 → 1,000
```

The batch size is subject to:

* User limits
* Provider limits
* Campaign policies
* Worker capacity

---

# 14. Large Campaign Strategy

For very large campaigns, avoid loading all recipients into memory.

Use streaming/chunked processing.

```text
Recipient Source
      ↓
Chunk Reader
      ↓
Validation
      ↓
Suppression Check
      ↓
Batch Builder
      ↓
Queue
```

Example:

```text
10,000,000 recipients

Read:
10,000

Process:
10,000

Queue:
10,000

Release memory

Read next:
10,000
```

---

# 15. SMTP / Provider Management

Provider records should include:

```text
id
provider_name
host
port
username
encrypted_secret
from_email
from_name

hourly_limit
daily_limit
per_second_limit

status
health_score

created_at
updated_at
```

Passwords and secrets must be encrypted at rest.

---

# 16. Provider Assignment

Providers can be assigned to users.

```text
User A
   ↓
Provider 01

User B
   ↓
Provider 02

User C
   ↓
Provider 03
```

Assignment table:

```text
smtp_assignments

id
user_id
provider_id
status
assigned_at
assigned_by
```

---

# 17. Provider State Machine

```text
ACTIVE
  │
  ├── WARNING
  │
  ├── DEGRADED
  │
  └── DISABLED
```

Example:

```text
Provider 01
Health: 98%
ACTIVE

Provider 02
Health: 55%
WARNING

Provider 03
Health: 12%
DISABLED
```

Admin can:

```text
Enable
Disable
Assign
Unassign
Test
Inspect
```

---

# 18. Provider Health Monitoring

Monitor:

```text
Authentication
Connection
Timeout
Provider response
Failure rate
Bounce rate
Deferrals
Complaints
```

Do not automatically disable a provider based on a single failure.

Use configurable thresholds and rolling windows.

---



# 20. Throughput Planning

Target:

```text
1,500,000 emails/hour
```

Equivalent average rate:

```text
1,500,000 / 3,600
≈ 416.67 messages/second
```

This is a capacity-planning target, not a guarantee.

The system should therefore support horizontal scaling.

Example:

```text
Worker 01 → 80/sec
Worker 02 → 80/sec
Worker 03 → 80/sec
Worker 04 → 80/sec
Worker 05 → 80/sec
Worker 06 → 80/sec
```

Total theoretical capacity:

```text
480/sec
```

Actual throughput will depend on:

* Provider limits
* Network
* SMTP response time
* Recipient validation
* Retry rate
* Worker CPU
* Provider policies

---

# 21. Load Balancing

Do not use IP rotation as the primary load-balancing mechanism.

Use capacity-aware job scheduling.

```text
Queue
  ↓
Scheduler
  ↓
Worker Capacity
  ↓
Provider Capacity
  ↓
Job Assignment
```

Example:

```text
Worker A
Capacity: 100/sec
Current: 90/sec

Worker B
Capacity: 100/sec
Current: 20/sec

Worker C
Capacity: 50/sec
Current: 50/sec
```

New work should preferentially go to available capacity while respecting provider/user limits.

---

# 22. Retry Architecture

Transient failures should be retried.

Example:

```text
Attempt 1
   ↓
Failed
   ↓
Wait
   ↓
Attempt 2
   ↓
Failed
   ↓
Wait
   ↓
Attempt 3
```

Use exponential backoff.

Example:

```text
30 sec
1 min
2 min
5 min
10 min
```

Maximum retry count must be configurable.

Permanent failures must go to a Dead Letter Queue.

---

# 23. Delivery Event Processing

The system should maintain an event pipeline.

```text
Provider Event
     ↓
Event Receiver
     ↓
Event Validator
     ↓
Event Processor
     ↓
Database
     ↓
Reports
```

Possible statuses:

```text
queued
processing
sent
delivered
deferred
bounced
failed
complained
unsubscribed
```

---

# 24. Suppression System

Suppression is a first-class component.

```text
suppression_list
```

Types:

```text
unsubscribe
hard_bounce
complaint
invalid
admin_blocked
```

Before sending:

```text
Recipient
   ↓
Suppression Check
   ↓
Blocked?
 ┌───────┴───────┐
 YES             NO
 ↓                ↓
SKIP             SEND
```

---

# 25. Database Architecture

Recommended primary database:

```text
PostgreSQL
```

Core tables:

```text
users
roles
permissions

campaigns
campaign_recipients
campaign_batches

providers
provider_credentials
provider_assignments
provider_health_logs

workers
worker_heartbeats

jobs
job_attempts

email_events

user_limits
subscriptions

suppression_list

audit_logs
system_settings
```

---

# 26. Users Table

```sql
users
-----
id
email
username
password_hash
status
role_id
created_at
updated_at
last_login_at
```

Passwords must use a secure password hashing algorithm such as Argon2id or bcrypt.

---

# 27. Campaign Table

```sql
campaigns
---------
id
user_id
name
subject
from_name
from_email
status
total_recipients
processed
delivered
failed
bounced
batch_size
created_at
started_at
completed_at
```

---

# 28. Jobs Table

```sql
jobs
----
id
campaign_id
worker_id
provider_id
status
batch_size
attempts
available_at
started_at
completed_at
created_at
```

Indexes must exist on frequently queried fields.

Example:

```sql
CREATE INDEX idx_jobs_status
ON jobs(status);

CREATE INDEX idx_jobs_available_at
ON jobs(available_at);

CREATE INDEX idx_campaign_user
ON campaigns(user_id);
```

---

# 29. Email Events

```sql
email_events
------------
id
campaign_id
job_id
recipient_id
provider_id
event_type
provider_message_id
error_code
error_message
created_at
```

For extremely high event volumes, consider partitioning and/or a dedicated analytics/event store.

---

# 30. Redis Architecture

Redis can be used for:

```text
Job Queue
Distributed Locks
Worker Heartbeats
Temporary Counters
Real-time Statistics
Idempotency
```

Example:

```text
queue:email
queue:retry
queue:dead
rate:user:{id}
rate:provider:{id}
worker:{id}:heartbeat
```

---

# 31. Idempotency

A job must not accidentally be processed twice.

Every job should have:

```text
job_id
attempt_id
idempotency_key
```

Worker processing must verify job ownership before executing.

Example:

```text
PENDING
   ↓
Atomic Claim
   ↓
PROCESSING
```

Only one worker should successfully claim a job.

---

# 32. Authentication Architecture

## Admin

```text
Email
Password
2FA
RBAC
Session/JWT
```

## User

```text
Email/Username
Password
Session/JWT
Optional 2FA
```

## Worker

```text
Worker ID
Secure Credential
Short-lived Access Token
```

Workers should never use normal user credentials.

---

# 33. API Architecture

## Admin API

```text
/api/v1/admin/users
/api/v1/admin/campaigns
/api/v1/admin/providers
/api/v1/admin/workers
/api/v1/admin/queues
/api/v1/admin/reports
/api/v1/admin/settings
```

## User API

```text
/api/v1/user/profile
/api/v1/user/campaigns
/api/v1/user/providers
/api/v1/user/jobs
/api/v1/user/reports
```

## Worker API

```text
/api/v1/worker/register
/api/v1/worker/heartbeat
/api/v1/worker/jobs/claim
/api/v1/worker/jobs/{id}/ack
/api/v1/worker/jobs/{id}/failure
/api/v1/worker/health
```

---

# 34. API Security

All production APIs must use:

```text
HTTPS
Authentication
Authorization
Input Validation
Request Size Limits
Audit Logging
```

Never expose:

```text
Database credentials
SMTP passwords
Redis credentials
JWT secrets
Encryption keys
```

to frontend applications.

---

# 35. Environment Variables

Example:

```env
APP_ENV=production

DATABASE_URL=
REDIS_URL=

JWT_SECRET=

ENCRYPTION_KEY=

ADMIN_API_URL=
WORKER_API_URL=

QUEUE_NAME=

LOG_LEVEL=INFO
```

Secrets must be stored in a secure secret-management system in production.

---

# 36. Repository Structure

Recommended monorepo:

```text
email-platform/
│
├── admin-web/
│   ├── src/
│   ├── public/
│   └── package.json
│
├── user-web/
│   ├── src/
│   ├── public/
│   └── package.json
│
├── backend/
│   ├── app/
│   │   ├── api/
│   │   ├── auth/
│   │   ├── models/
│   │   ├── schemas/
│   │   ├── services/
│   │   ├── repositories/
│   │   ├── queue/
│   │   ├── providers/
│   │   └── reports/
│   │
│   ├── migrations/
│   ├── tests/
│   └── main.py
│
├── worker/
│   ├── app/
│   │   ├── queue/
│   │   ├── sender/
│   │   ├── providers/
│   │   ├── retry/
│   │   ├── rate_limit/
│   │   ├── health/
│   │   └── reporting/
│   │
│   ├── tests/
│   └── worker.py
│
├── infrastructure/
│   ├── docker/
│   ├── nginx/
│   ├── monitoring/
│   └── deployment/
│
├── docs/
│
├── docker-compose.yml
├── .env.example
├── README.md
└── ARCHITECTURE.md
```

---

# 37. Recommended Technology Stack

## Backend

```text
Python
FastAPI
SQLAlchemy
Alembic
Pydantic
```

## Database

```text
PostgreSQL
```

## Queue

Start with:

```text
Redis
```

For larger event/job workloads, evaluate:

```text
RabbitMQ
Kafka
Cloud Queue
```

based on durability, ordering, replay, and operational requirements.

## Frontend

```text
React
TypeScript
Vite
Tailwind CSS
shadcn/ui
```

## Worker

```text
Python
asyncio
SMTP/Provider SDK
Redis/Message Broker
```

## Infrastructure

```text
Docker
Docker Compose
Nginx
Cloudflare
Linux
```

---

# 38. Docker Architecture

```text
docker-compose.yml

services:

  backend:
    ...

  admin:
    ...

  user:
    ...

  worker:
    ...

  postgres:
    ...

  redis:
    ...

  nginx:
    ...
```

Production workers should normally be independently scalable rather than permanently tied to the API container.

---

# 39. Deployment Architecture

## Control Server

```text
Internet
   ↓
Nginx
   ↓
Admin Web
   ↓
Backend API
   ↓
PostgreSQL
   ↓
Redis
```

## Worker Server

```text
Internet
   ↓
Worker Runtime
   ↓
Authenticated API
   ↓
Queue
   ↓
Provider
```

Worker nodes should have no unnecessary inbound public ports.

---

# 40. Scaling Strategy

Start:

```text
1 API
1 PostgreSQL
1 Redis
1 Worker
```

Then scale:

```text
2 API
1 PostgreSQL
2 Redis/HA configuration
5 Workers
```

Then:

```text
API Cluster
Database HA
Redis HA
Worker Cluster
Dedicated Event Processor
Dedicated Scheduler
Monitoring Cluster
```

---

# 41. Horizontal Worker Scaling

Worker count should be dynamic.

```text
Queue Depth
     ↓
Scheduler
     ↓
Worker Capacity
     ↓
Scale Up / Scale Down
```

Example:

```text
Queue < 10,000
→ 3 Workers

Queue 10,000–100,000
→ 10 Workers

Queue > 100,000
→ 20 Workers
```

Actual scaling thresholds should be benchmarked.

---

# 42. Real-Time Monitoring

Admin dashboard should display:

```text
Active Workers
Online Providers
Queue Depth
Emails/sec
Processed
Delivered
Failed
Bounced
Deferred
Complaints
Worker CPU
Worker Memory
Provider Health
```

Use:

```text
WebSocket
```

or:

```text
Server-Sent Events
```

for real-time updates.

---

# 43. Observability

Use structured logs.

Example:

```json
{
  "timestamp": "...",
  "service": "worker",
  "worker_id": "worker-001",
  "job_id": "JOB-123",
  "event": "job_completed",
  "duration_ms": 1520
}
```

Recommended components:

```text
Prometheus
Grafana
Loki
OpenTelemetry
```

The exact stack can be simplified for the first release.

---

# 44. Metrics

Important metrics:

```text
jobs_created_total
jobs_completed_total
jobs_failed_total

emails_processed_total
emails_delivered_total
emails_bounced_total
emails_failed_total

provider_errors_total
provider_latency
worker_cpu
worker_memory

queue_depth
queue_latency

sending_rate
delivery_rate
bounce_rate
complaint_rate
```

---

# 45. Admin Audit Logs

Every sensitive admin action must be recorded.

```text
admin_id
action
resource
resource_id
old_value
new_value
ip
user_agent
timestamp
```

Examples:

```text
USER_SUSPENDED
SMTP_DISABLED
CAMPAIGN_CREATED
PROVIDER_ASSIGNED
WORKER_DISABLED
LIMIT_CHANGED
```

---

# 46. Disaster Recovery

Database backups:

```text
Daily Full Backup
+
Point-in-Time Recovery
```

Important:

```text
PostgreSQL Backup
Redis Persistence
Configuration Backup
Secret Backup
Infrastructure Configuration
```

Test restoration periodically.

A backup that has never been restored should not be considered verified.

---

# 47. Failure Scenarios

## Worker Crash

```text
Worker crashes
 ↓
Heartbeat expires
 ↓
Worker marked OFFLINE
 ↓
Unfinished job becomes recoverable
 ↓
Another worker claims it
```

## Provider Failure

```text
Provider failure
 ↓
Provider health decreases
 ↓
New jobs stop/slow according to policy
 ↓
Admin alerted
 ↓
Provider disabled if threshold reached
```

## Redis Failure

The system must fail safely.

No job should be silently lost.

Use durable queue configuration where required.

## Database Failure

```text
Primary DB
 ↓
Recovery/Failover
 ↓
Application reconnect
```

---

# 48. Security Model

Security requirements:

```text
TLS everywhere
Password hashing
Encrypted secrets
RBAC
Least privilege
API authentication
Worker authentication
Input validation
Audit logging
Secure headers
CSRF protection where applicable
CORS restrictions
Database access restrictions
```

---

# 49. Role-Based Access Control

Example roles:

```text
SUPER_ADMIN
ADMIN
OPERATOR
USER
VIEWER
```

Permissions:

```text
users.read
users.write

campaigns.read
campaigns.write
campaigns.start
campaigns.stop

providers.read
providers.write

workers.read
workers.write

reports.read
settings.write
```

---

# 50. Campaign State Machine

```text
DRAFT
  ↓
READY
  ↓
QUEUED
  ↓
PROCESSING
  ↓
COMPLETED
```

Alternative:

```text
PROCESSING
   ↓
PAUSED
   ↓
PROCESSING
```

Failure:

```text
PROCESSING
   ↓
FAILED
```

Admin can stop:

```text
PROCESSING
   ↓
CANCELLED
```

---

# 51. User Workflow

```text
LOGIN
  ↓
Dashboard
  ↓
Assigned Campaign
  ↓
Select Provider
  ↓
Select Batch Size
  ↓
Review
  ↓
Start
  ↓
Queue
  ↓
Worker
  ↓
Sending
  ↓
Live Result
  ↓
Report
```

---

# 52. Admin Workflow

```text
LOGIN
  ↓
Dashboard
  ↓
Create User
  ↓
Configure Limits
  ↓
Configure Provider
  ↓
Assign Provider
  ↓
Create Campaign
  ↓
Assign Campaign
  ↓
Monitor Queue
  ↓
Monitor Workers
  ↓
Monitor Delivery
  ↓
Reports
```

---

# 53. API Versioning

All APIs should be versioned.

```text
/api/v1/
```

Future:

```text
/api/v2/
```

Never make breaking API changes without versioning or a migration strategy.

---

# 54. Pagination

Never return thousands of records in a single API response.

Use:

```text
cursor-based pagination
```

for high-volume datasets.

Example:

```text
GET /api/v1/admin/campaigns?limit=50&cursor=...
```

---

# 55. Large File Uploads

Recipient files should not pass through the API server unnecessarily.

Recommended:

```text
User
 ↓
Presigned Upload URL
 ↓
Object Storage
 ↓
Validation Worker
 ↓
Recipient Processing
```

Possible storage:

```text
S3
Cloudflare R2
MinIO
```

---

# 56. Recipient Processing

For large files:

```text
Upload
 ↓
Object Storage
 ↓
File Validation
 ↓
Parse
 ↓
Normalize
 ↓
Deduplicate
 ↓
Suppression Check
 ↓
Create Batches
 ↓
Queue
```

Do not keep million-row recipient files in application memory.

---

# 57. Data Partitioning

As event volume grows, consider PostgreSQL partitioning for:

```text
email_events
provider_health_logs
worker_heartbeats
audit_logs
```

Partition by:

```text
date
```

or another appropriate access pattern.

---

# 58. Caching

Cache:

```text
User permissions
Provider configuration
System settings
Dashboard summaries
Worker metadata
```

Never cache sensitive data without an explicit security design.


---

# 60. Scheduler

A dedicated scheduler may manage:

```text
Campaign start time
Delayed jobs
Retry jobs
Worker balancing
Provider availability
Quota enforcement
```

Architecture:

```text
Scheduler
   ↓
Queue
   ↓
Workers
```

---

# 61. Event-Driven Architecture

Prefer events instead of tightly coupled services.

Examples:

```text
CampaignCreated
JobCreated
JobClaimed
JobCompleted
EmailSent
EmailDelivered
EmailBounced
EmailComplained
WorkerOnline
WorkerOffline
ProviderDisabled
```

---

# 62. Development Phases

## Phase 1 — MVP

Build:

```text
Admin Login
User Login
Campaign
Provider Management
Basic Queue
Worker
Basic Sending
Basic Reports
```

## Phase 2

Add:

```text
Batch Processing
Retries
Worker Monitoring
Provider Health
Suppression
Audit Logs
```

## Phase 3

Add:

```text
Multiple Workers
Horizontal Scaling
Real-time Dashboard
Advanced Reports
Object Storage
Advanced Event Processing
```

## Phase 4

Add:

```text
High Availability
Database Replication
Queue HA
Automated Scaling
Advanced Observability
Disaster Recovery
```

---

# 63. Testing Strategy

## Unit Tests

Test:

```text
Authentication
Campaign service
Batch generator
Retry logic
Provider health
Suppression
```

## Integration Tests

Test:

```text
API → Database
API → Queue
Worker → Queue
Worker → Provider
Event → Database
```

## Load Tests

Simulate:

```text
10,000 jobs
100,000 recipients
500,000 recipients
1,000,000 recipients
```

Measure:

```text
Throughput
Latency
Queue delay
CPU
Memory
Database load
Redis load
Provider response
```

---

# 64. Performance Target

The architecture should be designed to support a target of:

```text
1,500,000 messages/hour
```

or approximately:

```text
416.67 messages/second average
```

The system should not hard-code this number.

Instead:

```text
CONFIGURATION
     ↓
Provider Limits
     ↓
User Limits
     ↓
Worker Capacity
     ↓
Scheduler
```

This makes the platform scalable beyond the initial target.

---

# 65. Recommended Production Topology

Initial production:

```text
                 Internet
                    │
                 Cloudflare
                    │
                  Nginx
                    │
          ┌─────────┴─────────┐
          │                   │
      Admin Web           User Web
          │                   │
          └─────────┬─────────┘
                    │
                 API Cluster
                    │
             ┌──────┴──────┐
             │             │
        PostgreSQL       Redis
             │             │
             └──────┬──────┘
                    │
                  Queue
                    │
        ┌───────────┼───────────┐
        │           │           │
     Worker-01   Worker-02   Worker-N
        │           │           │
        └───────────┼───────────┘
                    │
             Email Providers
```

---

# 66. Recommended MVP Repository

For the first version:

```text
email-platform/
│
├── admin-web/
├── user-web/
├── backend/
├── worker/
├── infrastructure/
├── docs/
│
├── ARCHITECTURE.md
├── API.md
├── DATABASE.md
├── WORKER.md
├── DEPLOYMENT.md
├── SECURITY.md
├── TESTING.md
├── CONTRIBUTING.md
├── .env.example
├── docker-compose.yml
└── README.md
```

---

# 67. Final Architecture Principle

The most important design rule is:

```text
ADMIN
  ↓
CONTROL API
  ↓
DATABASE + QUEUE
  ↓
SCHEDULER
  ↓
WORKER CLUSTER
  ↓
APPROVED PROVIDERS
  ↓
RECIPIENTS
  ↓
DELIVERY EVENTS
  ↓
REPORTING
```

Never build the platform around:

```text
Admin → Direct SMTP Sending
```

Instead build it around:

```text
Control Plane
+
Durable Queue
+
Stateless Workers
+
Provider Abstraction
+
Event Processing
```

This allows the system to scale from:

```text
10,000/hour
```

to:

```text
100,000/hour
```

to:

```text
1,500,000/hour+
```

by adding workers and provider capacity rather than rewriting the core application.

---

# 68. Architecture Success Criteria

The architecture is considered production-ready when:

* Admin and User applications are separated.
* Sending is completely decoupled from the Admin API.
* Workers can be added/removed without changing campaigns.
* Jobs are idempotent.
* Jobs survive worker failures.
* Provider failures do not stop the entire system.
* User quotas are enforced.
* Provider limits are enforced.
* Suppressed recipients are never intentionally sent to.
* Delivery events are recorded.
* Reports are eventually consistent and auditable.
* Secrets are encrypted.
* Worker credentials are isolated.
* Database backups are tested.
* Queue recovery is tested.
* Load testing validates the required throughput.
* Monitoring and alerting are operational.

---

## Architecture Summary

```text
┌───────────────────────────────────────────────────────────┐
│                     CONTROL PLANE                         │
│                                                           │
│ Admin Panel → Admin API → PostgreSQL                      │
│                     │                                     │
│                     └────────→ Redis / Queue              │
└───────────────────────────────┬───────────────────────────┘
                                │
                                ▼
┌───────────────────────────────────────────────────────────┐
│                       DATA PLANE                           │
│                                                           │
│ Scheduler → Jobs → Worker Cluster → Provider              │
│                                                           │
│ Worker 01 ─┐                                               │
│ Worker 02 ─┼──→ Approved Email Providers                  │
│ Worker 03 ─┤                                               │
│ Worker N  ─┘                                               │
└───────────────────────────────┬───────────────────────────┘
                                │
                                ▼
┌───────────────────────────────────────────────────────────┐
│                     EVENT PLANE                            │
│                                                           │
│ Delivery Events → Event Processor → PostgreSQL            │
│                                  ↓                        │
│                             Reports                        │
│                                  ↓                        │
│                         Admin + User UI                    │
└───────────────────────────────────────────────────────────┘
```

**Core principle:**

> **Separate control from execution, execution from delivery, and delivery from reporting.**

This separation is what allows the platform to scale safely and maintainably.
