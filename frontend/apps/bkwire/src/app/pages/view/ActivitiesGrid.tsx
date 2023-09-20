import React, { useCallback, useMemo, useState, useRef } from 'react';
import {
  GridActionsCellItem,
  GridColDef,
  GridRowParams,
  GridSlotsComponent,
  DataGrid as MUIDataGrid,
} from '@mui/x-data-grid';
import DownloadIcon from '@mui/icons-material/Download';
import { GridToolbar } from '../../components/dataGrid/DataGrid';
import { useFormatters } from '../../hooks/useFormatters';
import { Tooltip } from '@mui/material';
import {
  useGetDocket,
  useGetViewer,
  useGetUserFileAccess,
} from '../../api/api.hooks';
import { ActivityGridRoot } from './View.styled';
import { FileDownload, FileDownloadHandle } from './FileDownload';

interface ActivitiesGridProps {
  caseId: number;
  components?: Partial<GridSlotsComponent>;
}
export const ActivitiesGrid = ({ caseId, components }: ActivitiesGridProps) => {
  const { data: viewer } = useGetViewer();

  const { dateFormatter, activityFormatter, docsPagesFormatter } =
    useFormatters();

  const [search, setSearch] = useState('');

  const dldModal = useRef<FileDownloadHandle>(null);

  const { isLoading: rowsLoading, data: dockets } = useGetDocket(caseId);

  const { data: fileAccess, status: statusFileAccess } =
    useGetUserFileAccess(caseId);

  const data =
    typeof dockets !== 'undefined' && typeof fileAccess !== 'undefined'
      ? dockets.map((docket) => ({
          ...docket,
          isOwned:
            fileAccess.filter((f) => f.docket_entry_id === docket.id).length >
            0,
        }))
      : [];

  const getActions = useCallback(
    (params: GridRowParams) => {
      const canDownload =
        params.row.file_type === 'petition' ||
        params.row.file_type === 'creditors' ||
        viewer?.subscription === 'mvp';

      const hasDocs = Boolean(
        !rowsLoading &&
          data &&
          params.row.docs + params.row.pages > 0 &&
          params.row.doc_url
      );

      const btn = (
        <DownloadIcon
          color={
            canDownload
              ? params.row.isOwned
                ? 'success'
                : 'warning'
              : 'disabled'
          }
        />
      );

      const action = !canDownload ? (
        <Tooltip
          title="Needs MVP access!"
          placement="top"
          disableInteractive
          arrow
        >
          {btn}
        </Tooltip>
      ) : (
        btn
      );

      return hasDocs
        ? [
            <GridActionsCellItem
              icon={action}
              label="Download"
              onClick={() =>
                canDownload &&
                dldModal.current?.open(
                  params.row.case_id,
                  params.row.id,
                  params.row.doc_url,
                  params.row.docs,
                  params.row.file_type
                )
              }
            />,
          ]
        : [];
    },
    [data, rowsLoading, viewer?.subscription]
  );

  const columns: GridColDef[] = useMemo(
    () => [
      {
        field: 'entry_date',
        headerName: 'Date',
        headerAlign: 'left',
        align: 'left',
        flex: 1,
        type: 'date',
        hideable: false,
        resizable: false,
        renderCell: dateFormatter,
      },
      {
        field: 'activity',
        headerName: 'Activity',
        headerAlign: 'left',
        align: 'left',
        flex: 5,
        type: 'string',
        hideable: false,
        resizable: false,
        renderCell: activityFormatter,
      },
      {
        field: 'pages',
        headerName: 'Download',
        headerAlign: 'left',
        align: 'left',
        width: 90,
        type: 'string',
        hideable: true,
        sortable: false,
        resizable: false,
        renderCell: docsPagesFormatter('docs'),
      },
      {
        field: 'Actions',
        type: 'actions',
        width: 90,
        hideable: false,
        hideSortIcons: true,
        disableExport: true,
        disableReorder: true,
        sortable: false,
        filterable: false,
        groupable: false,
        getActions,
      },
    ],
    [getActions, dateFormatter, activityFormatter, docsPagesFormatter]
  );

  return (
    <>
      <ActivityGridRoot>
        <MUIDataGrid
          rows={data || []}
          columns={columns}
          getRowId={(r) => r.id}
          hideFooter={true}
          paginationMode="client"
          initialState={{
            sorting: {
              sortModel: [
                {
                  field: 'date_added',
                  sort: null,
                },
              ],
            },
          }}
          rowCount={data?.length || 0}
          loading={rowsLoading}
          components={{
            Toolbar: GridToolbar,
            ...components,
          }}
          componentsProps={{
            toolbar: {
              search: {
                search,
                setSearch,
              },
              export: {},
              refreshActivities: {
                caseId,
              },
              hideColumnsMenu: false,
            },
          }}
          rowHeight={110}
          headerHeight={40}
          scrollbarSize={17}
          disableVirtualization
          disableSelectionOnClick={true}
          disableColumnFilter={true}
        />
      </ActivityGridRoot>
      <FileDownload ref={dldModal} />
    </>
  );
};
