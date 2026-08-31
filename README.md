# VariantFPS → Knight Shorts

Owned-gameplay Shorts workflow for the Knight channel.

## One-month publishing plan

- Source: https://www.youtube.com/@VariantFPS/videos
- Destination: Knight (@real-knight)
- Cadence: **2 Shorts daily for 30 days**
- Pakistan-time slots: **1:00 PM** and **9:00 PM**
- Output: 1080x1920 H.264/AAC MP4, approximately 20 seconds
- Selection: high-action audio windows with duplicate and overlap prevention
- Policy: use only source originals controlled by the channel operator

`worker/build_month_queue.py` prepares 60 dated files plus CSV/JSON scheduling manifests. It intentionally stops when there is not enough unique source footage instead of recycling the same moment.

The former public-download worker remains manual because YouTube blocks unattended public downloads unpredictably. The reliable flow downloads owned originals through authenticated YouTube Studio, builds the month locally, quality-checks the clips, and schedules them directly in Knight's YouTube Studio. This avoids the third-party free-plan limit.

No files, secrets, workflows, or deployments from the **SardarWaliTeaStore** repository are used or modified.
