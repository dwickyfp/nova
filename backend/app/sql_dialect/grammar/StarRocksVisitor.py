# Generated from StarRocks.g4 by ANTLR 4.13.2
from antlr4 import *
if "." in __name__:
    from .StarRocksParser import StarRocksParser
else:
    from StarRocksParser import StarRocksParser

# This class defines a complete generic visitor for a parse tree produced by StarRocksParser.

class StarRocksVisitor(ParseTreeVisitor):

    # Visit a parse tree produced by StarRocksParser#sqlStatements.
    def visitSqlStatements(self, ctx:StarRocksParser.SqlStatementsContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#singleStatement.
    def visitSingleStatement(self, ctx:StarRocksParser.SingleStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#emptyStatement.
    def visitEmptyStatement(self, ctx:StarRocksParser.EmptyStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#statement.
    def visitStatement(self, ctx:StarRocksParser.StatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showPredicateClauses.
    def visitShowPredicateClauses(self, ctx:StarRocksParser.ShowPredicateClausesContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#useDatabaseStatement.
    def visitUseDatabaseStatement(self, ctx:StarRocksParser.UseDatabaseStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#useCatalogStatement.
    def visitUseCatalogStatement(self, ctx:StarRocksParser.UseCatalogStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#setCatalogStatement.
    def visitSetCatalogStatement(self, ctx:StarRocksParser.SetCatalogStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showDatabasesStatement.
    def visitShowDatabasesStatement(self, ctx:StarRocksParser.ShowDatabasesStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#alterDbQuotaStatement.
    def visitAlterDbQuotaStatement(self, ctx:StarRocksParser.AlterDbQuotaStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#alterDatabaseSetStatement.
    def visitAlterDatabaseSetStatement(self, ctx:StarRocksParser.AlterDatabaseSetStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#createDbStatement.
    def visitCreateDbStatement(self, ctx:StarRocksParser.CreateDbStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#dropDbStatement.
    def visitDropDbStatement(self, ctx:StarRocksParser.DropDbStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showCreateDbStatement.
    def visitShowCreateDbStatement(self, ctx:StarRocksParser.ShowCreateDbStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#alterDatabaseRenameStatement.
    def visitAlterDatabaseRenameStatement(self, ctx:StarRocksParser.AlterDatabaseRenameStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#recoverDbStmt.
    def visitRecoverDbStmt(self, ctx:StarRocksParser.RecoverDbStmtContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showDataStmt.
    def visitShowDataStmt(self, ctx:StarRocksParser.ShowDataStmtContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showDataDistributionStmt.
    def visitShowDataDistributionStmt(self, ctx:StarRocksParser.ShowDataDistributionStmtContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#createTableStatement.
    def visitCreateTableStatement(self, ctx:StarRocksParser.CreateTableStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#columnDesc.
    def visitColumnDesc(self, ctx:StarRocksParser.ColumnDescContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#charsetName.
    def visitCharsetName(self, ctx:StarRocksParser.CharsetNameContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#defaultDesc.
    def visitDefaultDesc(self, ctx:StarRocksParser.DefaultDescContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#generatedColumnDesc.
    def visitGeneratedColumnDesc(self, ctx:StarRocksParser.GeneratedColumnDescContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#indexDesc.
    def visitIndexDesc(self, ctx:StarRocksParser.IndexDescContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#engineDesc.
    def visitEngineDesc(self, ctx:StarRocksParser.EngineDescContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#charsetDesc.
    def visitCharsetDesc(self, ctx:StarRocksParser.CharsetDescContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#collateDesc.
    def visitCollateDesc(self, ctx:StarRocksParser.CollateDescContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#keyDesc.
    def visitKeyDesc(self, ctx:StarRocksParser.KeyDescContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#orderByDesc.
    def visitOrderByDesc(self, ctx:StarRocksParser.OrderByDescContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#columnNullable.
    def visitColumnNullable(self, ctx:StarRocksParser.ColumnNullableContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#typeWithNullable.
    def visitTypeWithNullable(self, ctx:StarRocksParser.TypeWithNullableContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#aggStateDesc.
    def visitAggStateDesc(self, ctx:StarRocksParser.AggStateDescContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#aggDesc.
    def visitAggDesc(self, ctx:StarRocksParser.AggDescContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#rollupDesc.
    def visitRollupDesc(self, ctx:StarRocksParser.RollupDescContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#rollupItem.
    def visitRollupItem(self, ctx:StarRocksParser.RollupItemContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#dupKeys.
    def visitDupKeys(self, ctx:StarRocksParser.DupKeysContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#fromRollup.
    def visitFromRollup(self, ctx:StarRocksParser.FromRollupContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#orReplace.
    def visitOrReplace(self, ctx:StarRocksParser.OrReplaceContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#ifNotExists.
    def visitIfNotExists(self, ctx:StarRocksParser.IfNotExistsContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#createTableAsSelectStatement.
    def visitCreateTableAsSelectStatement(self, ctx:StarRocksParser.CreateTableAsSelectStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#dropTableStatement.
    def visitDropTableStatement(self, ctx:StarRocksParser.DropTableStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#cleanTemporaryTableStatement.
    def visitCleanTemporaryTableStatement(self, ctx:StarRocksParser.CleanTemporaryTableStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#alterTableStatement.
    def visitAlterTableStatement(self, ctx:StarRocksParser.AlterTableStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#createIndexStatement.
    def visitCreateIndexStatement(self, ctx:StarRocksParser.CreateIndexStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#dropIndexStatement.
    def visitDropIndexStatement(self, ctx:StarRocksParser.DropIndexStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#indexType.
    def visitIndexType(self, ctx:StarRocksParser.IndexTypeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showTableStatement.
    def visitShowTableStatement(self, ctx:StarRocksParser.ShowTableStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showTemporaryTablesStatement.
    def visitShowTemporaryTablesStatement(self, ctx:StarRocksParser.ShowTemporaryTablesStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showCreateTableStatement.
    def visitShowCreateTableStatement(self, ctx:StarRocksParser.ShowCreateTableStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showColumnStatement.
    def visitShowColumnStatement(self, ctx:StarRocksParser.ShowColumnStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showTableStatusStatement.
    def visitShowTableStatusStatement(self, ctx:StarRocksParser.ShowTableStatusStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#refreshTableStatement.
    def visitRefreshTableStatement(self, ctx:StarRocksParser.RefreshTableStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showAlterStatement.
    def visitShowAlterStatement(self, ctx:StarRocksParser.ShowAlterStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#descTableStatement.
    def visitDescTableStatement(self, ctx:StarRocksParser.DescTableStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#createTableLikeStatement.
    def visitCreateTableLikeStatement(self, ctx:StarRocksParser.CreateTableLikeStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showIndexStatement.
    def visitShowIndexStatement(self, ctx:StarRocksParser.ShowIndexStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#recoverTableStatement.
    def visitRecoverTableStatement(self, ctx:StarRocksParser.RecoverTableStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#truncateTableStatement.
    def visitTruncateTableStatement(self, ctx:StarRocksParser.TruncateTableStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#cancelAlterTableStatement.
    def visitCancelAlterTableStatement(self, ctx:StarRocksParser.CancelAlterTableStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showPartitionsStatement.
    def visitShowPartitionsStatement(self, ctx:StarRocksParser.ShowPartitionsStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#recoverPartitionStatement.
    def visitRecoverPartitionStatement(self, ctx:StarRocksParser.RecoverPartitionStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#createViewStatement.
    def visitCreateViewStatement(self, ctx:StarRocksParser.CreateViewStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#alterViewStatement.
    def visitAlterViewStatement(self, ctx:StarRocksParser.AlterViewStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#dropViewStatement.
    def visitDropViewStatement(self, ctx:StarRocksParser.DropViewStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#columnNameWithComment.
    def visitColumnNameWithComment(self, ctx:StarRocksParser.ColumnNameWithCommentContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#createMlModelStatement.
    def visitCreateMlModelStatement(self, ctx:StarRocksParser.CreateMlModelStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#mlModelTypeClause.
    def visitMlModelTypeClause(self, ctx:StarRocksParser.MlModelTypeClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#mlModelInputClause.
    def visitMlModelInputClause(self, ctx:StarRocksParser.MlModelInputClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#mlModelTimestampClause.
    def visitMlModelTimestampClause(self, ctx:StarRocksParser.MlModelTimestampClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#mlModelTargetClause.
    def visitMlModelTargetClause(self, ctx:StarRocksParser.MlModelTargetClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#mlModelSeriesClause.
    def visitMlModelSeriesClause(self, ctx:StarRocksParser.MlModelSeriesClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#mlModelConfigClause.
    def visitMlModelConfigClause(self, ctx:StarRocksParser.MlModelConfigClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#mlModelCompactClause.
    def visitMlModelCompactClause(self, ctx:StarRocksParser.MlModelCompactClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#mlModelPropertyList.
    def visitMlModelPropertyList(self, ctx:StarRocksParser.MlModelPropertyListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#mlModelProperty.
    def visitMlModelProperty(self, ctx:StarRocksParser.MlModelPropertyContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#submitTaskStatement.
    def visitSubmitTaskStatement(self, ctx:StarRocksParser.SubmitTaskStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#alterTaskStatement.
    def visitAlterTaskStatement(self, ctx:StarRocksParser.AlterTaskStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#taskClause.
    def visitTaskClause(self, ctx:StarRocksParser.TaskClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#dropTaskStatement.
    def visitDropTaskStatement(self, ctx:StarRocksParser.DropTaskStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#taskScheduleDesc.
    def visitTaskScheduleDesc(self, ctx:StarRocksParser.TaskScheduleDescContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#taskAfterClause.
    def visitTaskAfterClause(self, ctx:StarRocksParser.TaskAfterClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#taskNameList.
    def visitTaskNameList(self, ctx:StarRocksParser.TaskNameListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#taskFinalizeClause.
    def visitTaskFinalizeClause(self, ctx:StarRocksParser.TaskFinalizeClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#taskWhenClause.
    def visitTaskWhenClause(self, ctx:StarRocksParser.TaskWhenClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#taskOverlapClause.
    def visitTaskOverlapClause(self, ctx:StarRocksParser.TaskOverlapClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#taskCronScheduleDesc.
    def visitTaskCronScheduleDesc(self, ctx:StarRocksParser.TaskCronScheduleDescContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#createMaterializedViewStatement.
    def visitCreateMaterializedViewStatement(self, ctx:StarRocksParser.CreateMaterializedViewStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#mvPartitionExprs.
    def visitMvPartitionExprs(self, ctx:StarRocksParser.MvPartitionExprsContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#materializedViewDesc.
    def visitMaterializedViewDesc(self, ctx:StarRocksParser.MaterializedViewDescContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showMaterializedViewsStatement.
    def visitShowMaterializedViewsStatement(self, ctx:StarRocksParser.ShowMaterializedViewsStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#dropMaterializedViewStatement.
    def visitDropMaterializedViewStatement(self, ctx:StarRocksParser.DropMaterializedViewStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#alterMaterializedViewStatement.
    def visitAlterMaterializedViewStatement(self, ctx:StarRocksParser.AlterMaterializedViewStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#addMVColumnClause.
    def visitAddMVColumnClause(self, ctx:StarRocksParser.AddMVColumnClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#dropMVColumnClause.
    def visitDropMVColumnClause(self, ctx:StarRocksParser.DropMVColumnClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#refreshMaterializedViewStatement.
    def visitRefreshMaterializedViewStatement(self, ctx:StarRocksParser.RefreshMaterializedViewStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#cancelRefreshMaterializedViewStatement.
    def visitCancelRefreshMaterializedViewStatement(self, ctx:StarRocksParser.CancelRefreshMaterializedViewStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#adminSetConfigStatement.
    def visitAdminSetConfigStatement(self, ctx:StarRocksParser.AdminSetConfigStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#adminSetReplicaStatusStatement.
    def visitAdminSetReplicaStatusStatement(self, ctx:StarRocksParser.AdminSetReplicaStatusStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#adminShowConfigStatement.
    def visitAdminShowConfigStatement(self, ctx:StarRocksParser.AdminShowConfigStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#adminShowAutomatedSnapshotStatement.
    def visitAdminShowAutomatedSnapshotStatement(self, ctx:StarRocksParser.AdminShowAutomatedSnapshotStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#adminShowReplicaDistributionStatement.
    def visitAdminShowReplicaDistributionStatement(self, ctx:StarRocksParser.AdminShowReplicaDistributionStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#adminShowReplicaStatusStatement.
    def visitAdminShowReplicaStatusStatement(self, ctx:StarRocksParser.AdminShowReplicaStatusStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#adminShowTabletStatusStatement.
    def visitAdminShowTabletStatusStatement(self, ctx:StarRocksParser.AdminShowTabletStatusStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#adminRepairTableStatement.
    def visitAdminRepairTableStatement(self, ctx:StarRocksParser.AdminRepairTableStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#adminCancelRepairTableStatement.
    def visitAdminCancelRepairTableStatement(self, ctx:StarRocksParser.AdminCancelRepairTableStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#adminCheckTabletsStatement.
    def visitAdminCheckTabletsStatement(self, ctx:StarRocksParser.AdminCheckTabletsStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#adminSetPartitionVersion.
    def visitAdminSetPartitionVersion(self, ctx:StarRocksParser.AdminSetPartitionVersionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#killStatement.
    def visitKillStatement(self, ctx:StarRocksParser.KillStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#syncStatement.
    def visitSyncStatement(self, ctx:StarRocksParser.SyncStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#adminSetAutomatedSnapshotOnStatement.
    def visitAdminSetAutomatedSnapshotOnStatement(self, ctx:StarRocksParser.AdminSetAutomatedSnapshotOnStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#adminSetAutomatedSnapshotOffStatement.
    def visitAdminSetAutomatedSnapshotOffStatement(self, ctx:StarRocksParser.AdminSetAutomatedSnapshotOffStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#adminAlterAutomatedSnapshotIntervalStatement.
    def visitAdminAlterAutomatedSnapshotIntervalStatement(self, ctx:StarRocksParser.AdminAlterAutomatedSnapshotIntervalStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#adminSkipCommittedTransactionStatement.
    def visitAdminSkipCommittedTransactionStatement(self, ctx:StarRocksParser.AdminSkipCommittedTransactionStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#alterSystemStatement.
    def visitAlterSystemStatement(self, ctx:StarRocksParser.AlterSystemStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#cancelAlterSystemStatement.
    def visitCancelAlterSystemStatement(self, ctx:StarRocksParser.CancelAlterSystemStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showComputeNodesStatement.
    def visitShowComputeNodesStatement(self, ctx:StarRocksParser.ShowComputeNodesStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#createExternalCatalogStatement.
    def visitCreateExternalCatalogStatement(self, ctx:StarRocksParser.CreateExternalCatalogStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showCreateExternalCatalogStatement.
    def visitShowCreateExternalCatalogStatement(self, ctx:StarRocksParser.ShowCreateExternalCatalogStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#dropExternalCatalogStatement.
    def visitDropExternalCatalogStatement(self, ctx:StarRocksParser.DropExternalCatalogStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showCatalogsStatement.
    def visitShowCatalogsStatement(self, ctx:StarRocksParser.ShowCatalogsStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#alterCatalogStatement.
    def visitAlterCatalogStatement(self, ctx:StarRocksParser.AlterCatalogStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#createStorageVolumeStatement.
    def visitCreateStorageVolumeStatement(self, ctx:StarRocksParser.CreateStorageVolumeStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#typeDesc.
    def visitTypeDesc(self, ctx:StarRocksParser.TypeDescContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#locationsDesc.
    def visitLocationsDesc(self, ctx:StarRocksParser.LocationsDescContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showStorageVolumesStatement.
    def visitShowStorageVolumesStatement(self, ctx:StarRocksParser.ShowStorageVolumesStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#dropStorageVolumeStatement.
    def visitDropStorageVolumeStatement(self, ctx:StarRocksParser.DropStorageVolumeStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#alterStorageVolumeStatement.
    def visitAlterStorageVolumeStatement(self, ctx:StarRocksParser.AlterStorageVolumeStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#alterStorageVolumeClause.
    def visitAlterStorageVolumeClause(self, ctx:StarRocksParser.AlterStorageVolumeClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#modifyStorageVolumePropertiesClause.
    def visitModifyStorageVolumePropertiesClause(self, ctx:StarRocksParser.ModifyStorageVolumePropertiesClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#modifyStorageVolumeCommentClause.
    def visitModifyStorageVolumeCommentClause(self, ctx:StarRocksParser.ModifyStorageVolumeCommentClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#descStorageVolumeStatement.
    def visitDescStorageVolumeStatement(self, ctx:StarRocksParser.DescStorageVolumeStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#setDefaultStorageVolumeStatement.
    def visitSetDefaultStorageVolumeStatement(self, ctx:StarRocksParser.SetDefaultStorageVolumeStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#updateFailPointStatusStatement.
    def visitUpdateFailPointStatusStatement(self, ctx:StarRocksParser.UpdateFailPointStatusStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showFailPointStatement.
    def visitShowFailPointStatement(self, ctx:StarRocksParser.ShowFailPointStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#createDictionaryStatement.
    def visitCreateDictionaryStatement(self, ctx:StarRocksParser.CreateDictionaryStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#dropDictionaryStatement.
    def visitDropDictionaryStatement(self, ctx:StarRocksParser.DropDictionaryStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#refreshDictionaryStatement.
    def visitRefreshDictionaryStatement(self, ctx:StarRocksParser.RefreshDictionaryStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showDictionaryStatement.
    def visitShowDictionaryStatement(self, ctx:StarRocksParser.ShowDictionaryStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#cancelRefreshDictionaryStatement.
    def visitCancelRefreshDictionaryStatement(self, ctx:StarRocksParser.CancelRefreshDictionaryStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#dictionaryColumnDesc.
    def visitDictionaryColumnDesc(self, ctx:StarRocksParser.DictionaryColumnDescContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#dictionaryName.
    def visitDictionaryName(self, ctx:StarRocksParser.DictionaryNameContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#alterClause.
    def visitAlterClause(self, ctx:StarRocksParser.AlterClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#addFrontendClause.
    def visitAddFrontendClause(self, ctx:StarRocksParser.AddFrontendClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#dropFrontendClause.
    def visitDropFrontendClause(self, ctx:StarRocksParser.DropFrontendClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#modifyFrontendHostClause.
    def visitModifyFrontendHostClause(self, ctx:StarRocksParser.ModifyFrontendHostClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#addBackendClause.
    def visitAddBackendClause(self, ctx:StarRocksParser.AddBackendClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#dropBackendClause.
    def visitDropBackendClause(self, ctx:StarRocksParser.DropBackendClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#decommissionBackendClause.
    def visitDecommissionBackendClause(self, ctx:StarRocksParser.DecommissionBackendClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#modifyBackendClause.
    def visitModifyBackendClause(self, ctx:StarRocksParser.ModifyBackendClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#addComputeNodeClause.
    def visitAddComputeNodeClause(self, ctx:StarRocksParser.AddComputeNodeClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#dropComputeNodeClause.
    def visitDropComputeNodeClause(self, ctx:StarRocksParser.DropComputeNodeClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#modifyBrokerClause.
    def visitModifyBrokerClause(self, ctx:StarRocksParser.ModifyBrokerClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#alterLoadErrorUrlClause.
    def visitAlterLoadErrorUrlClause(self, ctx:StarRocksParser.AlterLoadErrorUrlClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#createImageClause.
    def visitCreateImageClause(self, ctx:StarRocksParser.CreateImageClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#cleanTabletSchedQClause.
    def visitCleanTabletSchedQClause(self, ctx:StarRocksParser.CleanTabletSchedQClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#decommissionDiskClause.
    def visitDecommissionDiskClause(self, ctx:StarRocksParser.DecommissionDiskClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#cancelDecommissionDiskClause.
    def visitCancelDecommissionDiskClause(self, ctx:StarRocksParser.CancelDecommissionDiskClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#disableDiskClause.
    def visitDisableDiskClause(self, ctx:StarRocksParser.DisableDiskClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#cancelDisableDiskClause.
    def visitCancelDisableDiskClause(self, ctx:StarRocksParser.CancelDisableDiskClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#createIndexClause.
    def visitCreateIndexClause(self, ctx:StarRocksParser.CreateIndexClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#dropIndexClause.
    def visitDropIndexClause(self, ctx:StarRocksParser.DropIndexClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#tableRenameClause.
    def visitTableRenameClause(self, ctx:StarRocksParser.TableRenameClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#swapTableClause.
    def visitSwapTableClause(self, ctx:StarRocksParser.SwapTableClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#modifyPropertiesClause.
    def visitModifyPropertiesClause(self, ctx:StarRocksParser.ModifyPropertiesClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#modifyCommentClause.
    def visitModifyCommentClause(self, ctx:StarRocksParser.ModifyCommentClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#optimizeRange.
    def visitOptimizeRange(self, ctx:StarRocksParser.OptimizeRangeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#optimizeClause.
    def visitOptimizeClause(self, ctx:StarRocksParser.OptimizeClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#addColumnClause.
    def visitAddColumnClause(self, ctx:StarRocksParser.AddColumnClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#addPartitionColumnClause.
    def visitAddPartitionColumnClause(self, ctx:StarRocksParser.AddPartitionColumnClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#addColumnsClause.
    def visitAddColumnsClause(self, ctx:StarRocksParser.AddColumnsClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#dropColumnClause.
    def visitDropColumnClause(self, ctx:StarRocksParser.DropColumnClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#dropPartitionColumnClause.
    def visitDropPartitionColumnClause(self, ctx:StarRocksParser.DropPartitionColumnClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#modifyColumnClause.
    def visitModifyColumnClause(self, ctx:StarRocksParser.ModifyColumnClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#modifyColumnCommentClause.
    def visitModifyColumnCommentClause(self, ctx:StarRocksParser.ModifyColumnCommentClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#columnRenameClause.
    def visitColumnRenameClause(self, ctx:StarRocksParser.ColumnRenameClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#reorderColumnsClause.
    def visitReorderColumnsClause(self, ctx:StarRocksParser.ReorderColumnsClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#rollupRenameClause.
    def visitRollupRenameClause(self, ctx:StarRocksParser.RollupRenameClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#compactionClause.
    def visitCompactionClause(self, ctx:StarRocksParser.CompactionClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#subfieldName.
    def visitSubfieldName(self, ctx:StarRocksParser.SubfieldNameContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#nestedFieldName.
    def visitNestedFieldName(self, ctx:StarRocksParser.NestedFieldNameContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#addFieldClause.
    def visitAddFieldClause(self, ctx:StarRocksParser.AddFieldClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#dropFieldClause.
    def visitDropFieldClause(self, ctx:StarRocksParser.DropFieldClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#createOrReplaceTagClause.
    def visitCreateOrReplaceTagClause(self, ctx:StarRocksParser.CreateOrReplaceTagClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#createOrReplaceBranchClause.
    def visitCreateOrReplaceBranchClause(self, ctx:StarRocksParser.CreateOrReplaceBranchClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#dropBranchClause.
    def visitDropBranchClause(self, ctx:StarRocksParser.DropBranchClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#dropTagClause.
    def visitDropTagClause(self, ctx:StarRocksParser.DropTagClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#tableOperationClause.
    def visitTableOperationClause(self, ctx:StarRocksParser.TableOperationClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#tableOperationArg.
    def visitTableOperationArg(self, ctx:StarRocksParser.TableOperationArgContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#tagOptions.
    def visitTagOptions(self, ctx:StarRocksParser.TagOptionsContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#branchOptions.
    def visitBranchOptions(self, ctx:StarRocksParser.BranchOptionsContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#snapshotRetention.
    def visitSnapshotRetention(self, ctx:StarRocksParser.SnapshotRetentionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#refRetain.
    def visitRefRetain(self, ctx:StarRocksParser.RefRetainContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#maxSnapshotAge.
    def visitMaxSnapshotAge(self, ctx:StarRocksParser.MaxSnapshotAgeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#minSnapshotsToKeep.
    def visitMinSnapshotsToKeep(self, ctx:StarRocksParser.MinSnapshotsToKeepContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#snapshotId.
    def visitSnapshotId(self, ctx:StarRocksParser.SnapshotIdContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#timeUnit.
    def visitTimeUnit(self, ctx:StarRocksParser.TimeUnitContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#integer_list.
    def visitInteger_list(self, ctx:StarRocksParser.Integer_listContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#dropPersistentIndexClause.
    def visitDropPersistentIndexClause(self, ctx:StarRocksParser.DropPersistentIndexClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#splitTabletClause.
    def visitSplitTabletClause(self, ctx:StarRocksParser.SplitTabletClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#mergeTabletClause.
    def visitMergeTabletClause(self, ctx:StarRocksParser.MergeTabletClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#tabletGroupList.
    def visitTabletGroupList(self, ctx:StarRocksParser.TabletGroupListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#alterTableAutoIncrementClause.
    def visitAlterTableAutoIncrementClause(self, ctx:StarRocksParser.AlterTableAutoIncrementClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#addPartitionClause.
    def visitAddPartitionClause(self, ctx:StarRocksParser.AddPartitionClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#dropPartitionClause.
    def visitDropPartitionClause(self, ctx:StarRocksParser.DropPartitionClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#truncatePartitionClause.
    def visitTruncatePartitionClause(self, ctx:StarRocksParser.TruncatePartitionClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#modifyPartitionClause.
    def visitModifyPartitionClause(self, ctx:StarRocksParser.ModifyPartitionClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#replacePartitionClause.
    def visitReplacePartitionClause(self, ctx:StarRocksParser.ReplacePartitionClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#partitionRenameClause.
    def visitPartitionRenameClause(self, ctx:StarRocksParser.PartitionRenameClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#insertStatement.
    def visitInsertStatement(self, ctx:StarRocksParser.InsertStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#insertLabelOrColumnAliases.
    def visitInsertLabelOrColumnAliases(self, ctx:StarRocksParser.InsertLabelOrColumnAliasesContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#columnAliasesOrByName.
    def visitColumnAliasesOrByName(self, ctx:StarRocksParser.ColumnAliasesOrByNameContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#updateStatement.
    def visitUpdateStatement(self, ctx:StarRocksParser.UpdateStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#deleteStatement.
    def visitDeleteStatement(self, ctx:StarRocksParser.DeleteStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#createRoutineLoadStatement.
    def visitCreateRoutineLoadStatement(self, ctx:StarRocksParser.CreateRoutineLoadStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#alterRoutineLoadStatement.
    def visitAlterRoutineLoadStatement(self, ctx:StarRocksParser.AlterRoutineLoadStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#dataSource.
    def visitDataSource(self, ctx:StarRocksParser.DataSourceContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#loadProperties.
    def visitLoadProperties(self, ctx:StarRocksParser.LoadPropertiesContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#colSeparatorProperty.
    def visitColSeparatorProperty(self, ctx:StarRocksParser.ColSeparatorPropertyContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#rowDelimiterProperty.
    def visitRowDelimiterProperty(self, ctx:StarRocksParser.RowDelimiterPropertyContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#importColumns.
    def visitImportColumns(self, ctx:StarRocksParser.ImportColumnsContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#columnProperties.
    def visitColumnProperties(self, ctx:StarRocksParser.ColumnPropertiesContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#includeMetadata.
    def visitIncludeMetadata(self, ctx:StarRocksParser.IncludeMetadataContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#metadataItem.
    def visitMetadataItem(self, ctx:StarRocksParser.MetadataItemContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#metaKey.
    def visitMetaKey(self, ctx:StarRocksParser.MetaKeyContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#jobProperties.
    def visitJobProperties(self, ctx:StarRocksParser.JobPropertiesContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#dataSourceProperties.
    def visitDataSourceProperties(self, ctx:StarRocksParser.DataSourcePropertiesContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#stopRoutineLoadStatement.
    def visitStopRoutineLoadStatement(self, ctx:StarRocksParser.StopRoutineLoadStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#resumeRoutineLoadStatement.
    def visitResumeRoutineLoadStatement(self, ctx:StarRocksParser.ResumeRoutineLoadStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#pauseRoutineLoadStatement.
    def visitPauseRoutineLoadStatement(self, ctx:StarRocksParser.PauseRoutineLoadStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showRoutineLoadStatement.
    def visitShowRoutineLoadStatement(self, ctx:StarRocksParser.ShowRoutineLoadStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showRoutineLoadTaskStatement.
    def visitShowRoutineLoadTaskStatement(self, ctx:StarRocksParser.ShowRoutineLoadTaskStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showCreateRoutineLoadStatement.
    def visitShowCreateRoutineLoadStatement(self, ctx:StarRocksParser.ShowCreateRoutineLoadStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showStreamLoadStatement.
    def visitShowStreamLoadStatement(self, ctx:StarRocksParser.ShowStreamLoadStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#analyzeStatement.
    def visitAnalyzeStatement(self, ctx:StarRocksParser.AnalyzeStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#regularColumns.
    def visitRegularColumns(self, ctx:StarRocksParser.RegularColumnsContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#allColumns.
    def visitAllColumns(self, ctx:StarRocksParser.AllColumnsContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#predicateColumns.
    def visitPredicateColumns(self, ctx:StarRocksParser.PredicateColumnsContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#multiColumnSet.
    def visitMultiColumnSet(self, ctx:StarRocksParser.MultiColumnSetContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#dropStatsStatement.
    def visitDropStatsStatement(self, ctx:StarRocksParser.DropStatsStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#histogramStatement.
    def visitHistogramStatement(self, ctx:StarRocksParser.HistogramStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#analyzeHistogramStatement.
    def visitAnalyzeHistogramStatement(self, ctx:StarRocksParser.AnalyzeHistogramStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#dropHistogramStatement.
    def visitDropHistogramStatement(self, ctx:StarRocksParser.DropHistogramStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#createAnalyzeStatement.
    def visitCreateAnalyzeStatement(self, ctx:StarRocksParser.CreateAnalyzeStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#dropAnalyzeJobStatement.
    def visitDropAnalyzeJobStatement(self, ctx:StarRocksParser.DropAnalyzeJobStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showAnalyzeStatement.
    def visitShowAnalyzeStatement(self, ctx:StarRocksParser.ShowAnalyzeStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showStatsMetaStatement.
    def visitShowStatsMetaStatement(self, ctx:StarRocksParser.ShowStatsMetaStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showHistogramMetaStatement.
    def visitShowHistogramMetaStatement(self, ctx:StarRocksParser.ShowHistogramMetaStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#killAnalyzeStatement.
    def visitKillAnalyzeStatement(self, ctx:StarRocksParser.KillAnalyzeStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#analyzeProfileStatement.
    def visitAnalyzeProfileStatement(self, ctx:StarRocksParser.AnalyzeProfileStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#createBaselinePlanStatement.
    def visitCreateBaselinePlanStatement(self, ctx:StarRocksParser.CreateBaselinePlanStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#dropBaselinePlanStatement.
    def visitDropBaselinePlanStatement(self, ctx:StarRocksParser.DropBaselinePlanStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showBaselinePlanStatement.
    def visitShowBaselinePlanStatement(self, ctx:StarRocksParser.ShowBaselinePlanStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#disableBaselinePlanStatement.
    def visitDisableBaselinePlanStatement(self, ctx:StarRocksParser.DisableBaselinePlanStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#enableBaselinePlanStatement.
    def visitEnableBaselinePlanStatement(self, ctx:StarRocksParser.EnableBaselinePlanStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#createResourceGroupStatement.
    def visitCreateResourceGroupStatement(self, ctx:StarRocksParser.CreateResourceGroupStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#dropResourceGroupStatement.
    def visitDropResourceGroupStatement(self, ctx:StarRocksParser.DropResourceGroupStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#alterResourceGroupStatement.
    def visitAlterResourceGroupStatement(self, ctx:StarRocksParser.AlterResourceGroupStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showResourceGroupStatement.
    def visitShowResourceGroupStatement(self, ctx:StarRocksParser.ShowResourceGroupStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showResourceGroupUsageStatement.
    def visitShowResourceGroupUsageStatement(self, ctx:StarRocksParser.ShowResourceGroupUsageStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#createResourceStatement.
    def visitCreateResourceStatement(self, ctx:StarRocksParser.CreateResourceStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#alterResourceStatement.
    def visitAlterResourceStatement(self, ctx:StarRocksParser.AlterResourceStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#dropResourceStatement.
    def visitDropResourceStatement(self, ctx:StarRocksParser.DropResourceStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showResourceStatement.
    def visitShowResourceStatement(self, ctx:StarRocksParser.ShowResourceStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#classifier.
    def visitClassifier(self, ctx:StarRocksParser.ClassifierContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showFunctionsStatement.
    def visitShowFunctionsStatement(self, ctx:StarRocksParser.ShowFunctionsStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#dropFunctionStatement.
    def visitDropFunctionStatement(self, ctx:StarRocksParser.DropFunctionStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#createUdfFunctionStmt.
    def visitCreateUdfFunctionStmt(self, ctx:StarRocksParser.CreateUdfFunctionStmtContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#createInternalFunctionStmt.
    def visitCreateInternalFunctionStmt(self, ctx:StarRocksParser.CreateInternalFunctionStmtContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#inlineFunction.
    def visitInlineFunction(self, ctx:StarRocksParser.InlineFunctionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#typeList.
    def visitTypeList(self, ctx:StarRocksParser.TypeListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#functionArgsList.
    def visitFunctionArgsList(self, ctx:StarRocksParser.FunctionArgsListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#loadStatement.
    def visitLoadStatement(self, ctx:StarRocksParser.LoadStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#labelName.
    def visitLabelName(self, ctx:StarRocksParser.LabelNameContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#dataDescList.
    def visitDataDescList(self, ctx:StarRocksParser.DataDescListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#dataDesc.
    def visitDataDesc(self, ctx:StarRocksParser.DataDescContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#formatProps.
    def visitFormatProps(self, ctx:StarRocksParser.FormatPropsContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#brokerDesc.
    def visitBrokerDesc(self, ctx:StarRocksParser.BrokerDescContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#resourceDesc.
    def visitResourceDesc(self, ctx:StarRocksParser.ResourceDescContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showLoadStatement.
    def visitShowLoadStatement(self, ctx:StarRocksParser.ShowLoadStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showLoadWarningsStatement.
    def visitShowLoadWarningsStatement(self, ctx:StarRocksParser.ShowLoadWarningsStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#cancelLoadStatement.
    def visitCancelLoadStatement(self, ctx:StarRocksParser.CancelLoadStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#alterLoadStatement.
    def visitAlterLoadStatement(self, ctx:StarRocksParser.AlterLoadStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#cancelCompactionStatement.
    def visitCancelCompactionStatement(self, ctx:StarRocksParser.CancelCompactionStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showAuthorStatement.
    def visitShowAuthorStatement(self, ctx:StarRocksParser.ShowAuthorStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showBackendsStatement.
    def visitShowBackendsStatement(self, ctx:StarRocksParser.ShowBackendsStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showBrokerStatement.
    def visitShowBrokerStatement(self, ctx:StarRocksParser.ShowBrokerStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showCharsetStatement.
    def visitShowCharsetStatement(self, ctx:StarRocksParser.ShowCharsetStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showCollationStatement.
    def visitShowCollationStatement(self, ctx:StarRocksParser.ShowCollationStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showDeleteStatement.
    def visitShowDeleteStatement(self, ctx:StarRocksParser.ShowDeleteStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showDynamicPartitionStatement.
    def visitShowDynamicPartitionStatement(self, ctx:StarRocksParser.ShowDynamicPartitionStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showEventsStatement.
    def visitShowEventsStatement(self, ctx:StarRocksParser.ShowEventsStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showEnginesStatement.
    def visitShowEnginesStatement(self, ctx:StarRocksParser.ShowEnginesStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showFrontendsStatement.
    def visitShowFrontendsStatement(self, ctx:StarRocksParser.ShowFrontendsStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showPluginsStatement.
    def visitShowPluginsStatement(self, ctx:StarRocksParser.ShowPluginsStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showRepositoriesStatement.
    def visitShowRepositoriesStatement(self, ctx:StarRocksParser.ShowRepositoriesStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showOpenTableStatement.
    def visitShowOpenTableStatement(self, ctx:StarRocksParser.ShowOpenTableStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showPrivilegesStatement.
    def visitShowPrivilegesStatement(self, ctx:StarRocksParser.ShowPrivilegesStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showProcedureStatement.
    def visitShowProcedureStatement(self, ctx:StarRocksParser.ShowProcedureStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showProcStatement.
    def visitShowProcStatement(self, ctx:StarRocksParser.ShowProcStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showProcesslistStatement.
    def visitShowProcesslistStatement(self, ctx:StarRocksParser.ShowProcesslistStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showProfilelistStatement.
    def visitShowProfilelistStatement(self, ctx:StarRocksParser.ShowProfilelistStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showRunningQueriesStatement.
    def visitShowRunningQueriesStatement(self, ctx:StarRocksParser.ShowRunningQueriesStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showStatusStatement.
    def visitShowStatusStatement(self, ctx:StarRocksParser.ShowStatusStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showTabletStatement.
    def visitShowTabletStatement(self, ctx:StarRocksParser.ShowTabletStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showTransactionStatement.
    def visitShowTransactionStatement(self, ctx:StarRocksParser.ShowTransactionStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showTriggersStatement.
    def visitShowTriggersStatement(self, ctx:StarRocksParser.ShowTriggersStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showUserPropertyStatement.
    def visitShowUserPropertyStatement(self, ctx:StarRocksParser.ShowUserPropertyStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showVariablesStatement.
    def visitShowVariablesStatement(self, ctx:StarRocksParser.ShowVariablesStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showWarningStatement.
    def visitShowWarningStatement(self, ctx:StarRocksParser.ShowWarningStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#helpStatement.
    def visitHelpStatement(self, ctx:StarRocksParser.HelpStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#createUserStatement.
    def visitCreateUserStatement(self, ctx:StarRocksParser.CreateUserStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#dropUserStatement.
    def visitDropUserStatement(self, ctx:StarRocksParser.DropUserStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#alterUserStatement.
    def visitAlterUserStatement(self, ctx:StarRocksParser.AlterUserStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showUserStatement.
    def visitShowUserStatement(self, ctx:StarRocksParser.ShowUserStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showAllAuthentication.
    def visitShowAllAuthentication(self, ctx:StarRocksParser.ShowAllAuthenticationContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showAuthenticationForUser.
    def visitShowAuthenticationForUser(self, ctx:StarRocksParser.ShowAuthenticationForUserContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#executeAsStatement.
    def visitExecuteAsStatement(self, ctx:StarRocksParser.ExecuteAsStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#createRoleStatement.
    def visitCreateRoleStatement(self, ctx:StarRocksParser.CreateRoleStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#alterRoleStatement.
    def visitAlterRoleStatement(self, ctx:StarRocksParser.AlterRoleStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#dropRoleStatement.
    def visitDropRoleStatement(self, ctx:StarRocksParser.DropRoleStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showRolesStatement.
    def visitShowRolesStatement(self, ctx:StarRocksParser.ShowRolesStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#grantRoleToUser.
    def visitGrantRoleToUser(self, ctx:StarRocksParser.GrantRoleToUserContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#grantRoleToRole.
    def visitGrantRoleToRole(self, ctx:StarRocksParser.GrantRoleToRoleContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#grantRoleToGroup.
    def visitGrantRoleToGroup(self, ctx:StarRocksParser.GrantRoleToGroupContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#revokeRoleFromUser.
    def visitRevokeRoleFromUser(self, ctx:StarRocksParser.RevokeRoleFromUserContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#revokeRoleFromRole.
    def visitRevokeRoleFromRole(self, ctx:StarRocksParser.RevokeRoleFromRoleContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#revokeRoleFromGroup.
    def visitRevokeRoleFromGroup(self, ctx:StarRocksParser.RevokeRoleFromGroupContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#setRoleStatement.
    def visitSetRoleStatement(self, ctx:StarRocksParser.SetRoleStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#setDefaultRoleStatement.
    def visitSetDefaultRoleStatement(self, ctx:StarRocksParser.SetDefaultRoleStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#grantRevokeClause.
    def visitGrantRevokeClause(self, ctx:StarRocksParser.GrantRevokeClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#grantOnUser.
    def visitGrantOnUser(self, ctx:StarRocksParser.GrantOnUserContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#grantOnSystem.
    def visitGrantOnSystem(self, ctx:StarRocksParser.GrantOnSystemContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#grantOnTableBrief.
    def visitGrantOnTableBrief(self, ctx:StarRocksParser.GrantOnTableBriefContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#grantOnFunc.
    def visitGrantOnFunc(self, ctx:StarRocksParser.GrantOnFuncContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#grantOnPrimaryObj.
    def visitGrantOnPrimaryObj(self, ctx:StarRocksParser.GrantOnPrimaryObjContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#grantOnAll.
    def visitGrantOnAll(self, ctx:StarRocksParser.GrantOnAllContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#revokeOnUser.
    def visitRevokeOnUser(self, ctx:StarRocksParser.RevokeOnUserContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#revokeOnSystem.
    def visitRevokeOnSystem(self, ctx:StarRocksParser.RevokeOnSystemContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#revokeOnTableBrief.
    def visitRevokeOnTableBrief(self, ctx:StarRocksParser.RevokeOnTableBriefContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#revokeOnFunc.
    def visitRevokeOnFunc(self, ctx:StarRocksParser.RevokeOnFuncContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#revokeOnPrimaryObj.
    def visitRevokeOnPrimaryObj(self, ctx:StarRocksParser.RevokeOnPrimaryObjContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#revokeOnAll.
    def visitRevokeOnAll(self, ctx:StarRocksParser.RevokeOnAllContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showGrantsStatement.
    def visitShowGrantsStatement(self, ctx:StarRocksParser.ShowGrantsStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#authWithoutPlugin.
    def visitAuthWithoutPlugin(self, ctx:StarRocksParser.AuthWithoutPluginContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#authWithPlugin.
    def visitAuthWithPlugin(self, ctx:StarRocksParser.AuthWithPluginContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#privObjectName.
    def visitPrivObjectName(self, ctx:StarRocksParser.PrivObjectNameContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#privObjectNameList.
    def visitPrivObjectNameList(self, ctx:StarRocksParser.PrivObjectNameListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#privFunctionObjectNameList.
    def visitPrivFunctionObjectNameList(self, ctx:StarRocksParser.PrivFunctionObjectNameListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#privilegeTypeList.
    def visitPrivilegeTypeList(self, ctx:StarRocksParser.PrivilegeTypeListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#privilegeType.
    def visitPrivilegeType(self, ctx:StarRocksParser.PrivilegeTypeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#privObjectType.
    def visitPrivObjectType(self, ctx:StarRocksParser.PrivObjectTypeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#privObjectTypePlural.
    def visitPrivObjectTypePlural(self, ctx:StarRocksParser.PrivObjectTypePluralContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#createSecurityIntegrationStatement.
    def visitCreateSecurityIntegrationStatement(self, ctx:StarRocksParser.CreateSecurityIntegrationStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#alterSecurityIntegrationStatement.
    def visitAlterSecurityIntegrationStatement(self, ctx:StarRocksParser.AlterSecurityIntegrationStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#dropSecurityIntegrationStatement.
    def visitDropSecurityIntegrationStatement(self, ctx:StarRocksParser.DropSecurityIntegrationStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showSecurityIntegrationStatement.
    def visitShowSecurityIntegrationStatement(self, ctx:StarRocksParser.ShowSecurityIntegrationStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showCreateSecurityIntegrationStatement.
    def visitShowCreateSecurityIntegrationStatement(self, ctx:StarRocksParser.ShowCreateSecurityIntegrationStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#createGroupProviderStatement.
    def visitCreateGroupProviderStatement(self, ctx:StarRocksParser.CreateGroupProviderStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#dropGroupProviderStatement.
    def visitDropGroupProviderStatement(self, ctx:StarRocksParser.DropGroupProviderStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showGroupProvidersStatement.
    def visitShowGroupProvidersStatement(self, ctx:StarRocksParser.ShowGroupProvidersStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showCreateGroupProviderStatement.
    def visitShowCreateGroupProviderStatement(self, ctx:StarRocksParser.ShowCreateGroupProviderStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#backupStatement.
    def visitBackupStatement(self, ctx:StarRocksParser.BackupStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#cancelBackupStatement.
    def visitCancelBackupStatement(self, ctx:StarRocksParser.CancelBackupStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showBackupStatement.
    def visitShowBackupStatement(self, ctx:StarRocksParser.ShowBackupStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#restoreStatement.
    def visitRestoreStatement(self, ctx:StarRocksParser.RestoreStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#cancelRestoreStatement.
    def visitCancelRestoreStatement(self, ctx:StarRocksParser.CancelRestoreStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showRestoreStatement.
    def visitShowRestoreStatement(self, ctx:StarRocksParser.ShowRestoreStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showSnapshotStatement.
    def visitShowSnapshotStatement(self, ctx:StarRocksParser.ShowSnapshotStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#createRepositoryStatement.
    def visitCreateRepositoryStatement(self, ctx:StarRocksParser.CreateRepositoryStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#dropRepositoryStatement.
    def visitDropRepositoryStatement(self, ctx:StarRocksParser.DropRepositoryStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#addSqlBlackListStatement.
    def visitAddSqlBlackListStatement(self, ctx:StarRocksParser.AddSqlBlackListStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#delSqlBlackListStatement.
    def visitDelSqlBlackListStatement(self, ctx:StarRocksParser.DelSqlBlackListStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showSqlBlackListStatement.
    def visitShowSqlBlackListStatement(self, ctx:StarRocksParser.ShowSqlBlackListStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showWhiteListStatement.
    def visitShowWhiteListStatement(self, ctx:StarRocksParser.ShowWhiteListStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#addSqlDigestBlackListStatement.
    def visitAddSqlDigestBlackListStatement(self, ctx:StarRocksParser.AddSqlDigestBlackListStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#delSqlDigestBlackListStatement.
    def visitDelSqlDigestBlackListStatement(self, ctx:StarRocksParser.DelSqlDigestBlackListStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showSqlDigestBlackListStatement.
    def visitShowSqlDigestBlackListStatement(self, ctx:StarRocksParser.ShowSqlDigestBlackListStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#addBackendBlackListStatement.
    def visitAddBackendBlackListStatement(self, ctx:StarRocksParser.AddBackendBlackListStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#delBackendBlackListStatement.
    def visitDelBackendBlackListStatement(self, ctx:StarRocksParser.DelBackendBlackListStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showBackendBlackListStatement.
    def visitShowBackendBlackListStatement(self, ctx:StarRocksParser.ShowBackendBlackListStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#addComputeNodeBlackListStatement.
    def visitAddComputeNodeBlackListStatement(self, ctx:StarRocksParser.AddComputeNodeBlackListStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#delComputeNodeBlackListStatement.
    def visitDelComputeNodeBlackListStatement(self, ctx:StarRocksParser.DelComputeNodeBlackListStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showComputeNodeBlackListStatement.
    def visitShowComputeNodeBlackListStatement(self, ctx:StarRocksParser.ShowComputeNodeBlackListStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#dataCacheTarget.
    def visitDataCacheTarget(self, ctx:StarRocksParser.DataCacheTargetContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#createDataCacheRuleStatement.
    def visitCreateDataCacheRuleStatement(self, ctx:StarRocksParser.CreateDataCacheRuleStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showDataCacheRulesStatement.
    def visitShowDataCacheRulesStatement(self, ctx:StarRocksParser.ShowDataCacheRulesStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#dropDataCacheRuleStatement.
    def visitDropDataCacheRuleStatement(self, ctx:StarRocksParser.DropDataCacheRuleStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#clearDataCacheRulesStatement.
    def visitClearDataCacheRulesStatement(self, ctx:StarRocksParser.ClearDataCacheRulesStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#dataCacheSelectStatement.
    def visitDataCacheSelectStatement(self, ctx:StarRocksParser.DataCacheSelectStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#exportStatement.
    def visitExportStatement(self, ctx:StarRocksParser.ExportStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#cancelExportStatement.
    def visitCancelExportStatement(self, ctx:StarRocksParser.CancelExportStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showExportStatement.
    def visitShowExportStatement(self, ctx:StarRocksParser.ShowExportStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#installPluginStatement.
    def visitInstallPluginStatement(self, ctx:StarRocksParser.InstallPluginStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#uninstallPluginStatement.
    def visitUninstallPluginStatement(self, ctx:StarRocksParser.UninstallPluginStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#createFileStatement.
    def visitCreateFileStatement(self, ctx:StarRocksParser.CreateFileStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#dropFileStatement.
    def visitDropFileStatement(self, ctx:StarRocksParser.DropFileStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showSmallFilesStatement.
    def visitShowSmallFilesStatement(self, ctx:StarRocksParser.ShowSmallFilesStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#createPipeStatement.
    def visitCreatePipeStatement(self, ctx:StarRocksParser.CreatePipeStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#dropPipeStatement.
    def visitDropPipeStatement(self, ctx:StarRocksParser.DropPipeStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#alterPipeClause.
    def visitAlterPipeClause(self, ctx:StarRocksParser.AlterPipeClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#alterPipeStatement.
    def visitAlterPipeStatement(self, ctx:StarRocksParser.AlterPipeStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#descPipeStatement.
    def visitDescPipeStatement(self, ctx:StarRocksParser.DescPipeStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showPipeStatement.
    def visitShowPipeStatement(self, ctx:StarRocksParser.ShowPipeStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#setStatement.
    def visitSetStatement(self, ctx:StarRocksParser.SetStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#setNames.
    def visitSetNames(self, ctx:StarRocksParser.SetNamesContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#setPassword.
    def visitSetPassword(self, ctx:StarRocksParser.SetPasswordContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#setUserVar.
    def visitSetUserVar(self, ctx:StarRocksParser.SetUserVarContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#setSystemVar.
    def visitSetSystemVar(self, ctx:StarRocksParser.SetSystemVarContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#setTransaction.
    def visitSetTransaction(self, ctx:StarRocksParser.SetTransactionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#transaction_characteristics.
    def visitTransaction_characteristics(self, ctx:StarRocksParser.Transaction_characteristicsContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#transaction_access_mode.
    def visitTransaction_access_mode(self, ctx:StarRocksParser.Transaction_access_modeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#isolation_level.
    def visitIsolation_level(self, ctx:StarRocksParser.Isolation_levelContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#isolation_types.
    def visitIsolation_types(self, ctx:StarRocksParser.Isolation_typesContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#setExprOrDefault.
    def visitSetExprOrDefault(self, ctx:StarRocksParser.SetExprOrDefaultContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#refreshConnectionsStatement.
    def visitRefreshConnectionsStatement(self, ctx:StarRocksParser.RefreshConnectionsStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#setUserPropertyStatement.
    def visitSetUserPropertyStatement(self, ctx:StarRocksParser.SetUserPropertyStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#roleList.
    def visitRoleList(self, ctx:StarRocksParser.RoleListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#executeScriptStatement.
    def visitExecuteScriptStatement(self, ctx:StarRocksParser.ExecuteScriptStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#unsupportedStatement.
    def visitUnsupportedStatement(self, ctx:StarRocksParser.UnsupportedStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#lock_item.
    def visitLock_item(self, ctx:StarRocksParser.Lock_itemContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#lock_type.
    def visitLock_type(self, ctx:StarRocksParser.Lock_typeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#alterPlanAdvisorAddStatement.
    def visitAlterPlanAdvisorAddStatement(self, ctx:StarRocksParser.AlterPlanAdvisorAddStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#truncatePlanAdvisorStatement.
    def visitTruncatePlanAdvisorStatement(self, ctx:StarRocksParser.TruncatePlanAdvisorStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#alterPlanAdvisorDropStatement.
    def visitAlterPlanAdvisorDropStatement(self, ctx:StarRocksParser.AlterPlanAdvisorDropStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showPlanAdvisorStatement.
    def visitShowPlanAdvisorStatement(self, ctx:StarRocksParser.ShowPlanAdvisorStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#createWarehouseStatement.
    def visitCreateWarehouseStatement(self, ctx:StarRocksParser.CreateWarehouseStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#dropWarehouseStatement.
    def visitDropWarehouseStatement(self, ctx:StarRocksParser.DropWarehouseStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#suspendWarehouseStatement.
    def visitSuspendWarehouseStatement(self, ctx:StarRocksParser.SuspendWarehouseStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#resumeWarehouseStatement.
    def visitResumeWarehouseStatement(self, ctx:StarRocksParser.ResumeWarehouseStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#setWarehouseStatement.
    def visitSetWarehouseStatement(self, ctx:StarRocksParser.SetWarehouseStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showWarehousesStatement.
    def visitShowWarehousesStatement(self, ctx:StarRocksParser.ShowWarehousesStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showClustersStatement.
    def visitShowClustersStatement(self, ctx:StarRocksParser.ShowClustersStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#showNodesStatement.
    def visitShowNodesStatement(self, ctx:StarRocksParser.ShowNodesStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#alterWarehouseStatement.
    def visitAlterWarehouseStatement(self, ctx:StarRocksParser.AlterWarehouseStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#createCNGroupStatement.
    def visitCreateCNGroupStatement(self, ctx:StarRocksParser.CreateCNGroupStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#dropCNGroupStatement.
    def visitDropCNGroupStatement(self, ctx:StarRocksParser.DropCNGroupStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#enableCNGroupStatement.
    def visitEnableCNGroupStatement(self, ctx:StarRocksParser.EnableCNGroupStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#disableCNGroupStatement.
    def visitDisableCNGroupStatement(self, ctx:StarRocksParser.DisableCNGroupStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#alterCNGroupStatement.
    def visitAlterCNGroupStatement(self, ctx:StarRocksParser.AlterCNGroupStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#beginStatement.
    def visitBeginStatement(self, ctx:StarRocksParser.BeginStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#commitStatement.
    def visitCommitStatement(self, ctx:StarRocksParser.CommitStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#rollbackStatement.
    def visitRollbackStatement(self, ctx:StarRocksParser.RollbackStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#translateStatement.
    def visitTranslateStatement(self, ctx:StarRocksParser.TranslateStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#dialect.
    def visitDialect(self, ctx:StarRocksParser.DialectContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#translateSQL.
    def visitTranslateSQL(self, ctx:StarRocksParser.TranslateSQLContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#callProcedureStatement.
    def visitCallProcedureStatement(self, ctx:StarRocksParser.CallProcedureStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#queryStatement.
    def visitQueryStatement(self, ctx:StarRocksParser.QueryStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#queryRelation.
    def visitQueryRelation(self, ctx:StarRocksParser.QueryRelationContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#withClause.
    def visitWithClause(self, ctx:StarRocksParser.WithClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#queryNoWith.
    def visitQueryNoWith(self, ctx:StarRocksParser.QueryNoWithContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#queryPeriod.
    def visitQueryPeriod(self, ctx:StarRocksParser.QueryPeriodContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#periodType.
    def visitPeriodType(self, ctx:StarRocksParser.PeriodTypeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#queryWithParentheses.
    def visitQueryWithParentheses(self, ctx:StarRocksParser.QueryWithParenthesesContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#setOperation.
    def visitSetOperation(self, ctx:StarRocksParser.SetOperationContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#queryPrimaryDefault.
    def visitQueryPrimaryDefault(self, ctx:StarRocksParser.QueryPrimaryDefaultContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#subquery.
    def visitSubquery(self, ctx:StarRocksParser.SubqueryContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#rowConstructor.
    def visitRowConstructor(self, ctx:StarRocksParser.RowConstructorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#sortItem.
    def visitSortItem(self, ctx:StarRocksParser.SortItemContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#limitConstExpr.
    def visitLimitConstExpr(self, ctx:StarRocksParser.LimitConstExprContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#limitElement.
    def visitLimitElement(self, ctx:StarRocksParser.LimitElementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#querySpecification.
    def visitQuerySpecification(self, ctx:StarRocksParser.QuerySpecificationContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#from.
    def visitFrom(self, ctx:StarRocksParser.FromContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#dual.
    def visitDual(self, ctx:StarRocksParser.DualContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#rollup.
    def visitRollup(self, ctx:StarRocksParser.RollupContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#cube.
    def visitCube(self, ctx:StarRocksParser.CubeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#multipleGroupingSets.
    def visitMultipleGroupingSets(self, ctx:StarRocksParser.MultipleGroupingSetsContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#singleGroupingSet.
    def visitSingleGroupingSet(self, ctx:StarRocksParser.SingleGroupingSetContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#groupingSet.
    def visitGroupingSet(self, ctx:StarRocksParser.GroupingSetContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#commonTableExpression.
    def visitCommonTableExpression(self, ctx:StarRocksParser.CommonTableExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#setQuantifier.
    def visitSetQuantifier(self, ctx:StarRocksParser.SetQuantifierContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#selectSingle.
    def visitSelectSingle(self, ctx:StarRocksParser.SelectSingleContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#selectAll.
    def visitSelectAll(self, ctx:StarRocksParser.SelectAllContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#excludeClause.
    def visitExcludeClause(self, ctx:StarRocksParser.ExcludeClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#relations.
    def visitRelations(self, ctx:StarRocksParser.RelationsContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#relation.
    def visitRelation(self, ctx:StarRocksParser.RelationContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#tableAtom.
    def visitTableAtom(self, ctx:StarRocksParser.TableAtomContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#stageAtom.
    def visitStageAtom(self, ctx:StarRocksParser.StageAtomContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#inlineTable.
    def visitInlineTable(self, ctx:StarRocksParser.InlineTableContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#subqueryWithAlias.
    def visitSubqueryWithAlias(self, ctx:StarRocksParser.SubqueryWithAliasContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#tableFunction.
    def visitTableFunction(self, ctx:StarRocksParser.TableFunctionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#normalizedTableFunction.
    def visitNormalizedTableFunction(self, ctx:StarRocksParser.NormalizedTableFunctionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#fileTableFunction.
    def visitFileTableFunction(self, ctx:StarRocksParser.FileTableFunctionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#parenthesizedRelation.
    def visitParenthesizedRelation(self, ctx:StarRocksParser.ParenthesizedRelationContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#stageReference.
    def visitStageReference(self, ctx:StarRocksParser.StageReferenceContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#stageSeparator.
    def visitStageSeparator(self, ctx:StarRocksParser.StageSeparatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#stageSegment.
    def visitStageSegment(self, ctx:StarRocksParser.StageSegmentContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#stagePathAtom.
    def visitStagePathAtom(self, ctx:StarRocksParser.StagePathAtomContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#decimalAtom.
    def visitDecimalAtom(self, ctx:StarRocksParser.DecimalAtomContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#pivotClause.
    def visitPivotClause(self, ctx:StarRocksParser.PivotClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#pivotAggregationExpression.
    def visitPivotAggregationExpression(self, ctx:StarRocksParser.PivotAggregationExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#pivotValue.
    def visitPivotValue(self, ctx:StarRocksParser.PivotValueContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#sampleClause.
    def visitSampleClause(self, ctx:StarRocksParser.SampleClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#argumentList.
    def visitArgumentList(self, ctx:StarRocksParser.ArgumentListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#namedArgumentList.
    def visitNamedArgumentList(self, ctx:StarRocksParser.NamedArgumentListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#namedArguments.
    def visitNamedArguments(self, ctx:StarRocksParser.NamedArgumentsContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#joinRelation.
    def visitJoinRelation(self, ctx:StarRocksParser.JoinRelationContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#asofJoinType.
    def visitAsofJoinType(self, ctx:StarRocksParser.AsofJoinTypeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#crossOrInnerJoinType.
    def visitCrossOrInnerJoinType(self, ctx:StarRocksParser.CrossOrInnerJoinTypeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#outerAndSemiJoinType.
    def visitOuterAndSemiJoinType(self, ctx:StarRocksParser.OuterAndSemiJoinTypeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#bracketHint.
    def visitBracketHint(self, ctx:StarRocksParser.BracketHintContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#hintMap.
    def visitHintMap(self, ctx:StarRocksParser.HintMapContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#joinCriteria.
    def visitJoinCriteria(self, ctx:StarRocksParser.JoinCriteriaContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#columnAliases.
    def visitColumnAliases(self, ctx:StarRocksParser.ColumnAliasesContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#partitionNames.
    def visitPartitionNames(self, ctx:StarRocksParser.PartitionNamesContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#keyPartitionList.
    def visitKeyPartitionList(self, ctx:StarRocksParser.KeyPartitionListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#tabletList.
    def visitTabletList(self, ctx:StarRocksParser.TabletListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#prepareStatement.
    def visitPrepareStatement(self, ctx:StarRocksParser.PrepareStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#prepareSql.
    def visitPrepareSql(self, ctx:StarRocksParser.PrepareSqlContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#executeStatement.
    def visitExecuteStatement(self, ctx:StarRocksParser.ExecuteStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#deallocateStatement.
    def visitDeallocateStatement(self, ctx:StarRocksParser.DeallocateStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#replicaList.
    def visitReplicaList(self, ctx:StarRocksParser.ReplicaListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#expressionsWithDefault.
    def visitExpressionsWithDefault(self, ctx:StarRocksParser.ExpressionsWithDefaultContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#expressionOrDefault.
    def visitExpressionOrDefault(self, ctx:StarRocksParser.ExpressionOrDefaultContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#mapExpressionList.
    def visitMapExpressionList(self, ctx:StarRocksParser.MapExpressionListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#mapExpression.
    def visitMapExpression(self, ctx:StarRocksParser.MapExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#expressionSingleton.
    def visitExpressionSingleton(self, ctx:StarRocksParser.ExpressionSingletonContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#expressionDefault.
    def visitExpressionDefault(self, ctx:StarRocksParser.ExpressionDefaultContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#logicalNot.
    def visitLogicalNot(self, ctx:StarRocksParser.LogicalNotContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#logicalBinary.
    def visitLogicalBinary(self, ctx:StarRocksParser.LogicalBinaryContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#expressionList.
    def visitExpressionList(self, ctx:StarRocksParser.ExpressionListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#comparison.
    def visitComparison(self, ctx:StarRocksParser.ComparisonContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#booleanExpressionDefault.
    def visitBooleanExpressionDefault(self, ctx:StarRocksParser.BooleanExpressionDefaultContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#isNull.
    def visitIsNull(self, ctx:StarRocksParser.IsNullContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#scalarSubquery.
    def visitScalarSubquery(self, ctx:StarRocksParser.ScalarSubqueryContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#predicate.
    def visitPredicate(self, ctx:StarRocksParser.PredicateContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#tupleInSubquery.
    def visitTupleInSubquery(self, ctx:StarRocksParser.TupleInSubqueryContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#inIntegerList.
    def visitInIntegerList(self, ctx:StarRocksParser.InIntegerListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#inStringList.
    def visitInStringList(self, ctx:StarRocksParser.InStringListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#inSubquery.
    def visitInSubquery(self, ctx:StarRocksParser.InSubqueryContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#inList.
    def visitInList(self, ctx:StarRocksParser.InListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#between.
    def visitBetween(self, ctx:StarRocksParser.BetweenContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#like.
    def visitLike(self, ctx:StarRocksParser.LikeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#valueExpressionDefault.
    def visitValueExpressionDefault(self, ctx:StarRocksParser.ValueExpressionDefaultContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#arithmeticBinary.
    def visitArithmeticBinary(self, ctx:StarRocksParser.ArithmeticBinaryContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#dereference.
    def visitDereference(self, ctx:StarRocksParser.DereferenceContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#odbcFunctionCallExpression.
    def visitOdbcFunctionCallExpression(self, ctx:StarRocksParser.OdbcFunctionCallExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#matchExpr.
    def visitMatchExpr(self, ctx:StarRocksParser.MatchExprContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#columnRef.
    def visitColumnRef(self, ctx:StarRocksParser.ColumnRefContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#convert.
    def visitConvert(self, ctx:StarRocksParser.ConvertContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#collectionSubscript.
    def visitCollectionSubscript(self, ctx:StarRocksParser.CollectionSubscriptContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#literal.
    def visitLiteral(self, ctx:StarRocksParser.LiteralContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#cast.
    def visitCast(self, ctx:StarRocksParser.CastContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#parenthesizedExpression.
    def visitParenthesizedExpression(self, ctx:StarRocksParser.ParenthesizedExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#userVariableExpression.
    def visitUserVariableExpression(self, ctx:StarRocksParser.UserVariableExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#functionCallExpression.
    def visitFunctionCallExpression(self, ctx:StarRocksParser.FunctionCallExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#simpleCase.
    def visitSimpleCase(self, ctx:StarRocksParser.SimpleCaseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#arrowExpression.
    def visitArrowExpression(self, ctx:StarRocksParser.ArrowExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#systemVariableExpression.
    def visitSystemVariableExpression(self, ctx:StarRocksParser.SystemVariableExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#concat.
    def visitConcat(self, ctx:StarRocksParser.ConcatContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#subqueryExpression.
    def visitSubqueryExpression(self, ctx:StarRocksParser.SubqueryExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#lambdaFunctionExpr.
    def visitLambdaFunctionExpr(self, ctx:StarRocksParser.LambdaFunctionExprContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#dictionaryGetExpr.
    def visitDictionaryGetExpr(self, ctx:StarRocksParser.DictionaryGetExprContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#collate.
    def visitCollate(self, ctx:StarRocksParser.CollateContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#arrayConstructor.
    def visitArrayConstructor(self, ctx:StarRocksParser.ArrayConstructorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#mapConstructor.
    def visitMapConstructor(self, ctx:StarRocksParser.MapConstructorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#arraySlice.
    def visitArraySlice(self, ctx:StarRocksParser.ArraySliceContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#exists.
    def visitExists(self, ctx:StarRocksParser.ExistsContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#searchedCase.
    def visitSearchedCase(self, ctx:StarRocksParser.SearchedCaseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#arithmeticUnary.
    def visitArithmeticUnary(self, ctx:StarRocksParser.ArithmeticUnaryContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#numericLiteral.
    def visitNumericLiteral(self, ctx:StarRocksParser.NumericLiteralContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#nullLiteral.
    def visitNullLiteral(self, ctx:StarRocksParser.NullLiteralContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#booleanLiteral.
    def visitBooleanLiteral(self, ctx:StarRocksParser.BooleanLiteralContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#dateLiteral.
    def visitDateLiteral(self, ctx:StarRocksParser.DateLiteralContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#stringLiteral.
    def visitStringLiteral(self, ctx:StarRocksParser.StringLiteralContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#intervalLiteral.
    def visitIntervalLiteral(self, ctx:StarRocksParser.IntervalLiteralContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#binaryLiteral.
    def visitBinaryLiteral(self, ctx:StarRocksParser.BinaryLiteralContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#Parameter.
    def visitParameter(self, ctx:StarRocksParser.ParameterContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#generalLiteralExpression.
    def visitGeneralLiteralExpression(self, ctx:StarRocksParser.GeneralLiteralExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#extract.
    def visitExtract(self, ctx:StarRocksParser.ExtractContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#groupingOperation.
    def visitGroupingOperation(self, ctx:StarRocksParser.GroupingOperationContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#informationFunction.
    def visitInformationFunction(self, ctx:StarRocksParser.InformationFunctionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#specialDateTime.
    def visitSpecialDateTime(self, ctx:StarRocksParser.SpecialDateTimeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#specialFunction.
    def visitSpecialFunction(self, ctx:StarRocksParser.SpecialFunctionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#aggregationFunctionCall.
    def visitAggregationFunctionCall(self, ctx:StarRocksParser.AggregationFunctionCallContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#windowFunctionCall.
    def visitWindowFunctionCall(self, ctx:StarRocksParser.WindowFunctionCallContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#translateFunctionCall.
    def visitTranslateFunctionCall(self, ctx:StarRocksParser.TranslateFunctionCallContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#simpleFunctionCall.
    def visitSimpleFunctionCall(self, ctx:StarRocksParser.SimpleFunctionCallContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#aggregationFunction.
    def visitAggregationFunction(self, ctx:StarRocksParser.AggregationFunctionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#userVariable.
    def visitUserVariable(self, ctx:StarRocksParser.UserVariableContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#systemVariable.
    def visitSystemVariable(self, ctx:StarRocksParser.SystemVariableContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#columnReference.
    def visitColumnReference(self, ctx:StarRocksParser.ColumnReferenceContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#informationFunctionExpression.
    def visitInformationFunctionExpression(self, ctx:StarRocksParser.InformationFunctionExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#specialDateTimeExpression.
    def visitSpecialDateTimeExpression(self, ctx:StarRocksParser.SpecialDateTimeExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#specialFunctionExpression.
    def visitSpecialFunctionExpression(self, ctx:StarRocksParser.SpecialFunctionExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#windowFunction.
    def visitWindowFunction(self, ctx:StarRocksParser.WindowFunctionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#whenClause.
    def visitWhenClause(self, ctx:StarRocksParser.WhenClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#filter.
    def visitFilter(self, ctx:StarRocksParser.FilterContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#over.
    def visitOver(self, ctx:StarRocksParser.OverContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#ignoreNulls.
    def visitIgnoreNulls(self, ctx:StarRocksParser.IgnoreNullsContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#windowFrame.
    def visitWindowFrame(self, ctx:StarRocksParser.WindowFrameContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#unboundedFrame.
    def visitUnboundedFrame(self, ctx:StarRocksParser.UnboundedFrameContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#currentRowBound.
    def visitCurrentRowBound(self, ctx:StarRocksParser.CurrentRowBoundContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#boundedFrame.
    def visitBoundedFrame(self, ctx:StarRocksParser.BoundedFrameContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#backupRestoreObjectDesc.
    def visitBackupRestoreObjectDesc(self, ctx:StarRocksParser.BackupRestoreObjectDescContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#tableDesc.
    def visitTableDesc(self, ctx:StarRocksParser.TableDescContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#backupRestoreTableDesc.
    def visitBackupRestoreTableDesc(self, ctx:StarRocksParser.BackupRestoreTableDescContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#explainDesc.
    def visitExplainDesc(self, ctx:StarRocksParser.ExplainDescContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#optimizerTrace.
    def visitOptimizerTrace(self, ctx:StarRocksParser.OptimizerTraceContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#partitionExpr.
    def visitPartitionExpr(self, ctx:StarRocksParser.PartitionExprContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#partitionDesc.
    def visitPartitionDesc(self, ctx:StarRocksParser.PartitionDescContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#listPartitionDesc.
    def visitListPartitionDesc(self, ctx:StarRocksParser.ListPartitionDescContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#singleItemListPartitionDesc.
    def visitSingleItemListPartitionDesc(self, ctx:StarRocksParser.SingleItemListPartitionDescContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#multiItemListPartitionDesc.
    def visitMultiItemListPartitionDesc(self, ctx:StarRocksParser.MultiItemListPartitionDescContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#multiListPartitionValues.
    def visitMultiListPartitionValues(self, ctx:StarRocksParser.MultiListPartitionValuesContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#singleListPartitionValues.
    def visitSingleListPartitionValues(self, ctx:StarRocksParser.SingleListPartitionValuesContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#listPartitionValues.
    def visitListPartitionValues(self, ctx:StarRocksParser.ListPartitionValuesContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#listPartitionValue.
    def visitListPartitionValue(self, ctx:StarRocksParser.ListPartitionValueContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#stringList.
    def visitStringList(self, ctx:StarRocksParser.StringListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#integerList.
    def visitIntegerList(self, ctx:StarRocksParser.IntegerListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#literalExpressionList.
    def visitLiteralExpressionList(self, ctx:StarRocksParser.LiteralExpressionListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#generalLiteralExpressionList.
    def visitGeneralLiteralExpressionList(self, ctx:StarRocksParser.GeneralLiteralExpressionListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#rangePartitionDesc.
    def visitRangePartitionDesc(self, ctx:StarRocksParser.RangePartitionDescContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#singleRangePartition.
    def visitSingleRangePartition(self, ctx:StarRocksParser.SingleRangePartitionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#multiRangePartition.
    def visitMultiRangePartition(self, ctx:StarRocksParser.MultiRangePartitionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#partitionRangeDesc.
    def visitPartitionRangeDesc(self, ctx:StarRocksParser.PartitionRangeDescContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#partitionKeyDesc.
    def visitPartitionKeyDesc(self, ctx:StarRocksParser.PartitionKeyDescContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#partitionValueList.
    def visitPartitionValueList(self, ctx:StarRocksParser.PartitionValueListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#keyPartition.
    def visitKeyPartition(self, ctx:StarRocksParser.KeyPartitionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#partitionValue.
    def visitPartitionValue(self, ctx:StarRocksParser.PartitionValueContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#distributionClause.
    def visitDistributionClause(self, ctx:StarRocksParser.DistributionClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#distributionDesc.
    def visitDistributionDesc(self, ctx:StarRocksParser.DistributionDescContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#alterModifyDefaultBuckets.
    def visitAlterModifyDefaultBuckets(self, ctx:StarRocksParser.AlterModifyDefaultBucketsContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#refreshSchemeDesc.
    def visitRefreshSchemeDesc(self, ctx:StarRocksParser.RefreshSchemeDescContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#statusDesc.
    def visitStatusDesc(self, ctx:StarRocksParser.StatusDescContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#properties.
    def visitProperties(self, ctx:StarRocksParser.PropertiesContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#extProperties.
    def visitExtProperties(self, ctx:StarRocksParser.ExtPropertiesContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#propertyList.
    def visitPropertyList(self, ctx:StarRocksParser.PropertyListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#userPropertyList.
    def visitUserPropertyList(self, ctx:StarRocksParser.UserPropertyListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#property.
    def visitProperty(self, ctx:StarRocksParser.PropertyContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#inlineProperties.
    def visitInlineProperties(self, ctx:StarRocksParser.InlinePropertiesContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#inlineProperty.
    def visitInlineProperty(self, ctx:StarRocksParser.InlinePropertyContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#varType.
    def visitVarType(self, ctx:StarRocksParser.VarTypeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#comment.
    def visitComment(self, ctx:StarRocksParser.CommentContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#outfile.
    def visitOutfile(self, ctx:StarRocksParser.OutfileContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#fileFormat.
    def visitFileFormat(self, ctx:StarRocksParser.FileFormatContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#string.
    def visitString(self, ctx:StarRocksParser.StringContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#binary.
    def visitBinary(self, ctx:StarRocksParser.BinaryContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#comparisonOperator.
    def visitComparisonOperator(self, ctx:StarRocksParser.ComparisonOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#matchOperator.
    def visitMatchOperator(self, ctx:StarRocksParser.MatchOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#booleanValue.
    def visitBooleanValue(self, ctx:StarRocksParser.BooleanValueContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#interval.
    def visitInterval(self, ctx:StarRocksParser.IntervalContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#taskInterval.
    def visitTaskInterval(self, ctx:StarRocksParser.TaskIntervalContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#taskUnitIdentifier.
    def visitTaskUnitIdentifier(self, ctx:StarRocksParser.TaskUnitIdentifierContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#unitIdentifier.
    def visitUnitIdentifier(self, ctx:StarRocksParser.UnitIdentifierContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#filesSchema.
    def visitFilesSchema(self, ctx:StarRocksParser.FilesSchemaContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#filesSchemaColumn.
    def visitFilesSchemaColumn(self, ctx:StarRocksParser.FilesSchemaColumnContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#type.
    def visitType(self, ctx:StarRocksParser.TypeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#arrayType.
    def visitArrayType(self, ctx:StarRocksParser.ArrayTypeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#mapType.
    def visitMapType(self, ctx:StarRocksParser.MapTypeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#subfieldDesc.
    def visitSubfieldDesc(self, ctx:StarRocksParser.SubfieldDescContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#subfieldDescs.
    def visitSubfieldDescs(self, ctx:StarRocksParser.SubfieldDescsContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#structType.
    def visitStructType(self, ctx:StarRocksParser.StructTypeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#typeParameter.
    def visitTypeParameter(self, ctx:StarRocksParser.TypeParameterContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#baseType.
    def visitBaseType(self, ctx:StarRocksParser.BaseTypeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#decimalType.
    def visitDecimalType(self, ctx:StarRocksParser.DecimalTypeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#qualifiedName.
    def visitQualifiedName(self, ctx:StarRocksParser.QualifiedNameContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#tableName.
    def visitTableName(self, ctx:StarRocksParser.TableNameContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#writeBranch.
    def visitWriteBranch(self, ctx:StarRocksParser.WriteBranchContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#unquotedIdentifier.
    def visitUnquotedIdentifier(self, ctx:StarRocksParser.UnquotedIdentifierContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#digitIdentifier.
    def visitDigitIdentifier(self, ctx:StarRocksParser.DigitIdentifierContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#backQuotedIdentifier.
    def visitBackQuotedIdentifier(self, ctx:StarRocksParser.BackQuotedIdentifierContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#identifierWithAlias.
    def visitIdentifierWithAlias(self, ctx:StarRocksParser.IdentifierWithAliasContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#identifierWithAliasList.
    def visitIdentifierWithAliasList(self, ctx:StarRocksParser.IdentifierWithAliasListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#identifierList.
    def visitIdentifierList(self, ctx:StarRocksParser.IdentifierListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#identifierOrString.
    def visitIdentifierOrString(self, ctx:StarRocksParser.IdentifierOrStringContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#identifierOrStringList.
    def visitIdentifierOrStringList(self, ctx:StarRocksParser.IdentifierOrStringListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#identifierOrStringOrStar.
    def visitIdentifierOrStringOrStar(self, ctx:StarRocksParser.IdentifierOrStringOrStarContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#userWithoutHost.
    def visitUserWithoutHost(self, ctx:StarRocksParser.UserWithoutHostContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#userWithHost.
    def visitUserWithHost(self, ctx:StarRocksParser.UserWithHostContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#userWithHostAndBlanket.
    def visitUserWithHostAndBlanket(self, ctx:StarRocksParser.UserWithHostAndBlanketContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#assignment.
    def visitAssignment(self, ctx:StarRocksParser.AssignmentContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#assignmentList.
    def visitAssignmentList(self, ctx:StarRocksParser.AssignmentListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#integerValue.
    def visitIntegerValue(self, ctx:StarRocksParser.IntegerValueContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#decimalValue.
    def visitDecimalValue(self, ctx:StarRocksParser.DecimalValueContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#doubleValue.
    def visitDoubleValue(self, ctx:StarRocksParser.DoubleValueContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by StarRocksParser#nonReserved.
    def visitNonReserved(self, ctx:StarRocksParser.NonReservedContext):
        return self.visitChildren(ctx)



del StarRocksParser