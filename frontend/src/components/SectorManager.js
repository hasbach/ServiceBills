import React, { useState, useEffect, useCallback } from 'react';
import { Box, Typography, Button, TextField, List, ListItem, ListItemText, IconButton, Paper } from '@mui/material';
import { Add as AddIcon, Edit as EditIcon, Delete as DeleteIcon, Save as SaveIcon, Cancel as CancelIcon } from '@mui/icons-material';
import { useAppContext } from '../context/AppContext.js';

const SectorManager = () => {
    const { apiService, setSnackbar } = useAppContext();
    const [sectors, setSectors] = useState([]);
    const [newSectorName, setNewSectorName] = useState('');
    const [editingId, setEditingId] = useState(null);
    const [editingName, setEditingName] = useState('');

    const fetchSectors = useCallback(async () => {
        try {
            const response = await apiService.fetchSectors();
            setSectors(response.data);
        } catch (error) {
            setSnackbar({ open: true, message: 'Failed to fetch sectors.', severity: 'error' });
        }
    }, [apiService, setSnackbar]);

    useEffect(() => {
        fetchSectors();
    }, [fetchSectors]);

    const handleAddSector = async () => {
        if (!newSectorName.trim()) {
            setSnackbar({ open: true, message: 'Sector name cannot be empty.', severity: 'warning' });
            return;
        }
        try {
            await apiService.addSector({ name: newSectorName });
            setSnackbar({ open: true, message: 'Sector added successfully!', severity: 'success' });
            setNewSectorName('');
            fetchSectors();
        } catch (error) {
            setSnackbar({ open: true, message: error.response?.data?.error || 'Failed to add sector.', severity: 'error' });
        }
    };

    const handleDeleteSector = async (sectorId) => {
        if (window.confirm('Are you sure you want to delete this sector?')) {
            try {
                await apiService.deleteSector(sectorId);
                setSnackbar({ open: true, message: 'Sector deleted successfully!', severity: 'success' });
                fetchSectors();
            } catch (error) {
                setSnackbar({ open: true, message: error.response?.data?.error || 'Failed to delete sector.', severity: 'error' });
            }
        }
    };

    const handleEdit = (sector) => {
        setEditingId(sector.id);
        setEditingName(sector.name);
    };

    const handleCancelEdit = () => {
        setEditingId(null);
        setEditingName('');
    };

    const handleSaveEdit = async (sectorId) => {
        if (!editingName.trim()) {
            setSnackbar({ open: true, message: 'Sector name cannot be empty.', severity: 'warning' });
            return;
        }
        try {
            await apiService.updateSector(sectorId, { name: editingName });
            setSnackbar({ open: true, message: 'Sector updated successfully!', severity: 'success' });
            handleCancelEdit();
            fetchSectors();
        } catch (error) {
            setSnackbar({ open: true, message: error.response?.data?.error || 'Failed to update sector.', severity: 'error' });
        }
    };

    return (
        <Paper sx={{ p: 3, mt: 4 }}>
            <Typography variant="h6" gutterBottom>Manage Expense Sectors</Typography>
            <Box sx={{ display: 'flex', gap: 2, mb: 2 }}>
                <TextField
                    label="New Sector Name"
                    value={newSectorName}
                    onChange={(e) => setNewSectorName(e.target.value)}
                    size="small"
                    sx={{ flexGrow: 1 }}
                />
                <Button variant="contained" onClick={handleAddSector} startIcon={<AddIcon />}>Add</Button>
            </Box>
            <List>
                {sectors.map((sector) => (
                    <ListItem key={sector.id} secondaryAction={
                        editingId === sector.id ? (
                            <>
                                <IconButton edge="end" onClick={() => handleSaveEdit(sector.id)}><SaveIcon color="primary" /></IconButton>
                                <IconButton edge="end" onClick={handleCancelEdit}><CancelIcon /></IconButton>
                            </>
                        ) : (
                            <>
                                <IconButton edge="end" onClick={() => handleEdit(sector)}><EditIcon /></IconButton>
                                <IconButton edge="end" onClick={() => handleDeleteSector(sector.id)}><DeleteIcon color="error" /></IconButton>
                            </>
                        )
                    }>
                        {editingId === sector.id ? (
                            <TextField
                                value={editingName}
                                onChange={(e) => setEditingName(e.target.value)}
                                size="small"
                                autoFocus
                            />
                        ) : (
                            <ListItemText primary={sector.name} />
                        )}
                    </ListItem>
                ))}
            </List>
        </Paper>
    );
};

export default SectorManager;

