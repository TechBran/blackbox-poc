Restart the AI BlackBox Flight Recorder service.

Run the following command to restart the service:

```bash
sudo systemctl restart blackbox.service
```

After restarting, confirm the service is running by checking its status. If the restart is successful, inform the user that the BlackBox service has been restarted and they should hard refresh the portal (Ctrl+Shift+R) to load the latest changes.
