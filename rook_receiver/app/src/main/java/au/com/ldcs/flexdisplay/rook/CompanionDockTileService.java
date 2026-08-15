package au.com.ldcs.flexdisplay.rook;

import android.content.Intent;
import android.service.quicksettings.Tile;
import android.service.quicksettings.TileService;

public final class CompanionDockTileService extends TileService {
    static final String ACTION_SHOW_DOCK_CENTRE =
            "au.com.ldcs.flexdisplay.rook.action.SHOW_DOCK_CENTRE";

    @Override
    public void onStartListening() {
        super.onStartListening();
        updateTile();
    }

    @Override
    public void onClick() {
        super.onClick();
        if (CompanionPreferences.dockMode(this)) {
            // The tile may always make the receiver less persistent without
            // opening the app or starting network/media work.
            CompanionPreferences.setDockMode(this, false);
            updateTile();
            return;
        }
        // Enabling requires a visible Activity and another local confirmation.
        Intent intent = new Intent(this, MainActivity.class)
                .setAction(ACTION_SHOW_DOCK_CENTRE)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        startActivityAndCollapse(intent);
    }

    private void updateTile() {
        Tile tile = getQsTile();
        if (tile == null) return;
        boolean enabled = CompanionPreferences.dockMode(this);
        tile.setState(enabled ? Tile.STATE_ACTIVE : Tile.STATE_INACTIVE);
        tile.setLabel(enabled ? "FlexDisplay Dock on" : "FlexDisplay Dock");
        tile.updateTile();
    }
}
